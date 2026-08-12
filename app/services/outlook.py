import logging
import datetime
from datetime import timedelta, datetime as dt, time as dt_time
from typing import List, Dict, Any, Optional
import httpx

from app.config import settings

logger = logging.getLogger("biztechbot")

STANDARD_SLOTS = [
    "09:30 AM",
    "10:00 AM",
    "10:30 AM",
    "11:00 AM",
    "11:30 AM",
    "12:00 PM",
    "02:00 PM",
    "02:30 PM",
    "03:00 PM",
    "03:30 PM",
    "04:00 PM",
    "04:30 PM",
    "05:00 PM",
    "05:30 PM",
]


class OutlookService:
    _token_cache: Optional[str] = None
    _token_expires_at: Optional[dt] = None

    @classmethod
    async def get_access_token(cls) -> Optional[str]:
        """Fetch Microsoft Graph OAuth2 access token using client credentials grant."""
        now = dt.now(datetime.timezone.utc)
        if cls._token_cache and cls._token_expires_at and cls._token_expires_at > now + timedelta(seconds=60):
            return cls._token_cache

        tenant_id = settings.AZURE_TENANT_ID
        client_id = settings.AZURE_CLIENT_ID
        client_secret = settings.AZURE_CLIENT_SECRET

        if not (tenant_id and client_id and client_secret):
            logger.warning("Microsoft Graph credentials incomplete in settings")
            return None

        tenants_to_try = [tenant_id, "biztechnosys.com", "organizations"]
        tenants_to_try = list(dict.fromkeys([t for t in tenants_to_try if t]))

        async with httpx.AsyncClient(timeout=15.0) as client:
            for t_id in tenants_to_try:
                token_url = f"https://login.microsoftonline.com/{t_id}/oauth2/v2.0/token"
                payload = {
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "scope": "https://graph.microsoft.com/.default",
                }
                try:
                    res = await client.post(token_url, data=payload)
                    if res.status_code == 200:
                        data = res.json()
                        access_token = data.get("access_token")
                        expires_in = data.get("expires_in", 3600)
                        cls._token_cache = access_token
                        cls._token_expires_at = now + timedelta(seconds=expires_in)
                        logger.info(f"Successfully obtained MS Graph access token (tenant: {t_id})")
                        return access_token
                    else:
                        logger.warning(f"Tenant '{t_id}' token request returned {res.status_code}: {res.text[:150]}")
                except Exception as e:
                    logger.error(f"Exception requesting MS Graph token for tenant '{t_id}': {e}")
            return None

    @classmethod
    def parse_slot_time(cls, date_obj: datetime.date, time_str: str) -> dt:
        """Parse time string like '09:30 AM' or '03:30 PM' into a datetime object."""
        time_part = dt.strptime(time_str.strip(), "%I:%M %p").time()
        return dt.combine(date_obj, time_part)

    @classmethod
    async def get_busy_intervals_from_graph(cls, date_str: str) -> List[tuple[dt, dt]]:
        """Query Microsoft Graph API for calendar events/schedules of chethana@biztechnosys.com on date_str."""
        token = await cls.get_access_token()
        if not token:
            logger.warning("No MS Graph access token available; skipping calendar busy slot query.")
            return []

        email = settings.OUTLOOK_EMAIL
        start_time_iso = f"{date_str}T00:00:00Z"
        end_time_iso = f"{date_str}T23:59:59Z"

        url = f"https://graph.microsoft.com/v1.0/users/{email}/calendarView?startDateTime={start_time_iso}&endDateTime={end_time_iso}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Prefer": 'outlook.timezone="Asia/Kolkata"',
        }

        busy_intervals = []

        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                res = await client.get(url, headers=headers)
                if res.status_code == 200:
                    data = res.json()
                    events = data.get("value", [])
                    logger.info(f"Retrieved {len(events)} Outlook calendar events for {date_str}")
                    for event in events:
                        start_raw = event.get("start", {}).get("dateTime")
                        end_raw = event.get("end", {}).get("dateTime")
                        if start_raw and end_raw:
                            try:
                                # Truncate fractional seconds for clean parsing
                                s_clean = start_raw.split(".")[0].rstrip("Z")
                                e_clean = end_raw.split(".")[0].rstrip("Z")
                                s_dt = dt.fromisoformat(s_clean)
                                e_dt = dt.fromisoformat(e_clean)
                                busy_intervals.append((s_dt, e_dt))
                            except Exception as parse_err:
                                logger.warning(f"Error parsing event datetimes '{start_raw}', '{end_raw}': {parse_err}")
                else:
                    logger.warning(f"MS Graph calendarView request returned {res.status_code}: {res.text}")
            except Exception as e:
                logger.error(f"Error fetching Outlook calendar events via MS Graph: {e}")

        return busy_intervals

    @classmethod
    async def get_available_slots(cls, date_str: str) -> Dict[str, Any]:
        """
        Calculates available appointment slots for date_str (YYYY-MM-DD):
        Rule 1: Never allow booking within the next 24 hours from current time.
        Rule 2: Read existing Outlook events via MS Graph for chethana@biztechnosys.com and remove busy slots.
        """
        try:
            target_date = dt.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return {"slots": [], "message": "Invalid date format. Use YYYY-MM-DD."}

        # Current time reference
        now_local = dt.now()
        min_allowed_time = now_local + timedelta(hours=24)

        # Retrieve busy slots from Outlook Graph API
        busy_intervals = await cls.get_busy_intervals_from_graph(date_str)

        available_slots = []
        for slot_str in STANDARD_SLOTS:
            slot_dt = cls.parse_slot_time(target_date, slot_str)

            # Rule 1 Check: Must be >= now + 24 hours
            if slot_dt < min_allowed_time:
                continue

            # Rule 2 Check: Must not overlap with any busy interval
            slot_end = slot_dt + timedelta(minutes=30)
            is_busy = False
            for b_start, b_end in busy_intervals:
                # Remove timezone info for naive comparison if needed
                bs = b_start.replace(tzinfo=None)
                be = b_end.replace(tzinfo=None)
                if not (slot_end <= bs or slot_dt >= be):
                    is_busy = True
                    break

            if not is_busy:
                available_slots.append(slot_str)

        if not available_slots:
            return {
                "slots": [],
                "message": "No appointments are available for this date. Please choose another date."
            }

        return {
            "date": date_str,
            "slots": available_slots,
            "message": "Available slots retrieved successfully."
        }

    @classmethod
    async def create_calendar_event(
        cls,
        lead_name: str,
        user_email: str,
        company_name: str,
        date_str: str,
        time_str: str,
        notes: str = ""
    ) -> bool:
        """Schedule a meeting in Outlook Calendar using Microsoft Graph API."""
        token = await cls.get_access_token()
        if not token:
            logger.warning("MS Graph token unavailable. Skipping direct calendar event creation.")
            return False

        try:
            target_date = dt.strptime(date_str, "%Y-%m-%d").date()
            start_dt = cls.parse_slot_time(target_date, time_str)
            end_dt = start_dt + timedelta(minutes=30)
        except Exception as e:
            logger.error(f"Error building event datetimes: {e}")
            return False

        email = settings.OUTLOOK_EMAIL
        url = f"https://graph.microsoft.com/v1.0/users/{email}/events"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        event_payload = {
            "subject": f"Discovery Call with {lead_name} ({company_name or 'Client'})",
            "body": {
                "contentType": "HTML",
                "content": f"""
                    <h3>Discovery Call Scheduled via BizBot AI Assistant</h3>
                    <p><b>Lead Name:</b> {lead_name}</p>
                    <p><b>Company:</b> {company_name or 'N/A'}</p>
                    <p><b>Client Email:</b> {user_email}</p>
                    <p><b>Appointment Date & Time:</b> {date_str} at {time_str}</p>
                    <p><b>Project Notes:</b> {notes or 'N/A'}</p>
                """
            },
            "start": {
                "dateTime": start_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                "timeZone": "Asia/Kolkata"
            },
            "end": {
                "dateTime": end_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                "timeZone": "Asia/Kolkata"
            },
            "attendees": [
                {
                    "emailAddress": {"address": user_email, "name": lead_name},
                    "type": "required"
                },
                {
                    "emailAddress": {"address": email, "name": "Biztechnosys Team"},
                    "type": "required"
                }
            ],
            "isOnlineMeeting": True,
            "onlineMeetingProvider": "teamsForBusiness"
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                res = await client.post(url, headers=headers, json=event_payload)
                if res.status_code in (200, 201):
                    logger.info(f"Successfully scheduled Outlook Calendar event for {lead_name}")
                    return True
                else:
                    logger.error(f"MS Graph create event failed with {res.status_code}: {res.text}")
                    return False
            except Exception as e:
                logger.error(f"Exception creating Outlook Calendar event: {e}")
                return False

    @classmethod
    async def send_confirmation_emails(
        cls,
        lead_name: str,
        user_email: str,
        company_name: str,
        date_str: str,
        time_str: str,
        notes: str = ""
    ) -> bool:
        """Send confirmation emails to both the user and chethana@biztechnosys.com using MS Graph sendMail API."""
        token = await cls.get_access_token()
        if not token:
            logger.warning("MS Graph token unavailable. Skipping automated email delivery.")
            return False

        email = settings.OUTLOOK_EMAIL
        url = f"https://graph.microsoft.com/v1.0/users/{email}/sendMail"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        html_content = f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; padding: 20px; border: 1px solid #e2e8f0; border-radius: 8px;">
                <h2 style="color: #1e293b; margin-top: 0;">Discovery Call Confirmed! 🚀</h2>
                <p>Hello <b>{lead_name}</b>,</p>
                <p>Thank you for booking a Discovery Call with Biztechnosys. Our team looks forward to discussing your project requirement.</p>
                
                <div style="background-color: #f8fafc; padding: 15px; border-left: 4px solid #3b82f6; margin: 20px 0;">
                    <p style="margin: 5px 0;"><b>📅 Date:</b> {date_str}</p>
                    <p style="margin: 5px 0;"><b>⏰ Time:</b> {time_str} (IST)</p>
                    <p style="margin: 5px 0;"><b>🏢 Company:</b> {company_name or 'N/A'}</p>
                    <p style="margin: 5px 0;"><b>📋 Requirements:</b> {notes or 'N/A'}</p>
                </div>
                
                <p>If you need to reschedule or have any questions before the meeting, please reply directly to this email.</p>
                <p>Best regards,<br><b>Biztechnosys Sales Team</b><br><a href="https://beta.biztechnosys.com">www.biztechnosys.com</a></p>
            </div>
        """

        recipients = [
            {"emailAddress": {"address": email}},
        ]
        if user_email and "@" in user_email:
            recipients.append({"emailAddress": {"address": user_email}})

        mail_payload = {
            "message": {
                "subject": f"Discovery Call Booking Confirmed - {lead_name} ({date_str} {time_str})",
                "body": {
                    "contentType": "HTML",
                    "content": html_content
                },
                "toRecipients": recipients
            },
            "saveToSentItems": "true"
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                res = await client.post(url, headers=headers, json=mail_payload)
                if res.status_code in (200, 202):
                    logger.info(f"Confirmation emails sent to {user_email} and {email}")
                    return True
                else:
                    logger.error(f"MS Graph sendMail failed with {res.status_code}: {res.text}")
                    return False
            except Exception as e:
                logger.error(f"Exception sending confirmation emails: {e}")
                return False
