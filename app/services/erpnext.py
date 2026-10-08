import json
import logging
import re
from typing import Optional, Dict, Any
from datetime import datetime, timezone, timedelta
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception
from app.config import settings

logger = logging.getLogger("biztechbot")

def is_retryable_error(exc: BaseException) -> bool:
    """Determine if HTTP request error is temporary and retryable."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.ConnectError))

COUNTRY_ALIASES = {
    "usa": "United States",
    "us": "United States",
    "united states of america": "United States",
    "u.s.": "United States",
    "u.s.a.": "United States",
    "uk": "United Kingdom",
    "united kingdom": "United Kingdom",
    "great britain": "United Kingdom",
    "britain": "United Kingdom",
    "england": "United Kingdom",
    "uae": "United Arab Emirates",
    "united arab emirates": "United Arab Emirates",
    "india": "India",
    "in": "India",
    "australia": "Australia",
    "singapore": "Singapore",
    "canada": "Canada",
    "germany": "Germany",
    "france": "France",
    "saudi arabia": "Saudi Arabia",
    "ksa": "Saudi Arabia",
    "qatar": "Qatar",
    "bahrain": "Bahrain",
    "egypt": "Egypt",
    "netherlands": "Netherlands",
    "switzerland": "Switzerland",
}

def normalize_country(country: Optional[str]) -> str:
    if not country or not country.strip():
        return ""
    c = country.strip()
    return COUNTRY_ALIASES.get(c.lower(), c.title())

class ERPNextService:
    @staticmethod
    def _get_headers() -> Dict[str, str]:
        return {
            "Authorization": f"token {settings.ERPNEXT_API_KEY}:{settings.ERPNEXT_API_SECRET}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _to_unicode_bold(text: str) -> str:
        """Convert ASCII letters and digits to their Unicode Mathematical Bold equivalents.

        These render as visually bold in any plain-text field without HTML support.
        Characters without a bold variant (punctuation, spaces, etc.) are kept as-is.
        """
        result = []
        for ch in text:
            if 'A' <= ch <= 'Z':
                result.append(chr(0x1D400 + ord(ch) - ord('A')))
            elif 'a' <= ch <= 'z':
                result.append(chr(0x1D41A + ord(ch) - ord('a')))
            elif '0' <= ch <= '9':
                result.append(chr(0x1D7CE + ord(ch) - ord('0')))
            else:
                result.append(ch)
        return ''.join(result)

    @staticmethod
    def _from_unicode_bold(text: str) -> str:
        """Convert Unicode Mathematical Bold characters back to plain ASCII for comparison."""
        result = []
        for ch in text:
            cp = ord(ch)
            if 0x1D400 <= cp <= 0x1D419:      # Bold A-Z
                result.append(chr(ord('A') + cp - 0x1D400))
            elif 0x1D41A <= cp <= 0x1D433:     # Bold a-z
                result.append(chr(ord('a') + cp - 0x1D41A))
            elif 0x1D7CE <= cp <= 0x1D7D7:     # Bold 0-9
                result.append(chr(ord('0') + cp - 0x1D7CE))
            else:
                result.append(ch)
        return ''.join(result)

    @classmethod
    def _format_bot_service_entry(cls, notes: str) -> str:
        """Format a notes string into a one-line entry for custom_bot_service.

        Format: [𝟎𝟕-𝟎𝟕-𝟐𝟎𝟐𝟔 | 𝟏𝟎:𝟒𝟑 𝐀𝐌] notes text
        The date/time portion uses Unicode bold characters for visual emphasis.
        """
        if not notes or not notes.strip():
            return ""

        IST = timezone(timedelta(hours=5, minutes=30))
        timestamp = datetime.now(IST)
        date_str = timestamp.strftime("%d-%m-%Y")
        time_str = timestamp.strftime("%I:%M %p")

        bold_timestamp = cls._to_unicode_bold(f"[{date_str} | {time_str}]")

        return f"{bold_timestamp} {notes.strip()}"

    @staticmethod
    def _convert_date_to_erp_format(date_str: str) -> str:
        """Convert a date string to ERPNext YYYY-MM-DD format.
        Handles DD-MM-YYYY, YYYY-MM-DD, and combined 'YYYY-MM-DD HH:MM AM/PM' formats.
        """
        if not date_str or not date_str.strip():
            return ""
        date_clean = date_str.strip()
        # Strip off time portion if combined (e.g. "2026-09-18 03:00 PM")
        date_part = date_clean.split(" ")[0]
        try:
            parsed = datetime.strptime(date_part, "%Y-%m-%d")
            return parsed.strftime("%Y-%m-%d")
        except ValueError:
            pass
        try:
            parsed = datetime.strptime(date_part, "%d-%m-%Y")
            return parsed.strftime("%Y-%m-%d")
        except ValueError:
            logger.warning(f"Could not parse appointment date: '{date_clean}'")
            return ""

    @staticmethod
    def _split_appointment_datetime(appointment_str: str) -> tuple[str, str]:
        """Split a combined appointment string into (erp_date, time_str).

        Input format: "2026-09-18 03:00 PM"  (date time am/pm)
        Returns: ("2026-09-18", "03:00 PM")  or ("", "") if unparseable.
        """
        if not appointment_str or not appointment_str.strip():
            return "", ""
        parts = appointment_str.strip().split(" ", 1)  # split on first space
        if len(parts) < 2:
            return "", ""
        date_part, time_part = parts[0], parts[1].strip()
        # Validate date portion is YYYY-MM-DD
        try:
            datetime.strptime(date_part, "%Y-%m-%d")
        except ValueError:
            logger.warning(f"Could not parse appointment datetime: '{appointment_str}'")
            return "", ""
        return date_part, time_part

    @classmethod
    def _extract_plain_notes(cls, custom_bot_service: str) -> str:
        """Strip all formatting (Unicode bold, timestamps, separators) from existing
        custom_bot_service text, returning only the raw note content for dedup comparison."""
        if not custom_bot_service:
            return ""
        plain = cls._from_unicode_bold(custom_bot_service)
        plain = re.sub(r'<[^>]+>', '', plain)
        plain = re.sub(r'\[\d{2}-\d{2}-\d{4}\s*\|\s*\d{1,2}:\d{2}\s*[APap][Mm]\]', '', plain)
        plain = re.sub(r'\[\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\]', '', plain)
        plain = plain.replace('---', '')
        plain = re.sub(r'\s+', ' ', plain).strip()
        return plain

    @classmethod
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception(is_retryable_error),
        reraise=True,
    )
    async def get_lead_by_email(cls, email: str) -> Optional[str]:
        """Search for an existing Lead by email address in ERPNext. Returns the lead ID/name if found."""
        if not email:
            return None

        headers = cls._get_headers()
        params = {
            "filters": json.dumps([["email_id", "=", email]])
        }
        
        async with httpx.AsyncClient(timeout=10, verify=settings.ERPNEXT_SSL_VERIFY) as client:
            try:
                resp = await client.get(
                    f"{settings.ERPNEXT_URL}/api/resource/Lead",
                    headers=headers,
                    params=params,
                )
                resp.raise_for_status()
                data = resp.json().get("data", [])
                if data:
                    return data[0].get("name")
            except httpx.HTTPStatusError as e:
                logger.error(f"ERPNext query status error {e.response.status_code}: {e.response.text}")
                raise
            except Exception as e:
                logger.error(f"Error querying ERPNext lead by email: {e}")
                raise
        return None

    @classmethod
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception(is_retryable_error),
        reraise=True,
    )
    async def get_lead_by_phone(cls, phone: str) -> Optional[str]:
        """Search for an existing Lead by mobile number in ERPNext. Returns the lead ID/name if found."""
        if not phone:
            return None

        headers = cls._get_headers()
        params = {
            "filters": json.dumps([["mobile_no", "=", phone]])
        }
        
        async with httpx.AsyncClient(timeout=10, verify=settings.ERPNEXT_SSL_VERIFY) as client:
            try:
                resp = await client.get(
                    f"{settings.ERPNEXT_URL}/api/resource/Lead",
                    headers=headers,
                    params=params,
                )
                resp.raise_for_status()
                data = resp.json().get("data", [])
                if data:
                    return data[0].get("name")
            except httpx.HTTPStatusError as e:
                logger.error(f"ERPNext query status error {e.response.status_code}: {e.response.text}")
                raise
            except Exception as e:
                logger.error(f"Error querying ERPNext lead by phone: {e}")
                raise
        return None

    @classmethod
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception(is_retryable_error),
        reraise=True,
    )
    async def get_lead_by_name_and_company(cls, lead_name: str, company_name: str) -> Optional[str]:
        """Search for an existing Lead by first_name + company_name in ERPNext. Returns the lead ID/name if found."""
        if not lead_name or not company_name:
            return None

        headers = cls._get_headers()
        params = {
            "filters": json.dumps([
                ["first_name", "=", lead_name],
                ["company_name", "=", company_name]
            ])
        }
        
        async with httpx.AsyncClient(timeout=10, verify=settings.ERPNEXT_SSL_VERIFY) as client:
            try:
                resp = await client.get(
                    f"{settings.ERPNEXT_URL}/api/resource/Lead",
                    headers=headers,
                    params=params,
                )
                resp.raise_for_status()
                data = resp.json().get("data", [])
                if data:
                    return data[0].get("name")
            except httpx.HTTPStatusError as e:
                logger.error(f"ERPNext query status error {e.response.status_code}: {e.response.text}")
                raise
            except Exception as e:
                logger.error(f"Error querying ERPNext lead by name+company: {e}")
                raise
        return None

    @classmethod
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception(is_retryable_error),
        reraise=True,
    )
    async def create_lead(cls, lead_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new Lead in ERPNext.
        
        Creates a lead with whatever fields are available. Only first_name is
        truly mandatory for ERPNext; email, phone, and company are optional.
        """
        full_notes = lead_data.get("notes", "")
        formatted_note = cls._format_bot_service_entry(full_notes)

        raw_country = lead_data.get("country", "")
        norm_country = normalize_country(raw_country)
        payload = {
            "doctype": "Lead",
            "salutation": "",
            "first_name": lead_data.get("lead_name", ""),
            "email_id": lead_data.get("email", ""),
            "company_name": lead_data.get("company_name", ""),
            "country": norm_country,
            "custom_bot_service": formatted_note,
            "source": "Bot",
        }
        
        phone_no = lead_data.get("phone", "")
        if phone_no:
            payload["mobile_no"] = phone_no
            payload["phone"] = phone_no
        
        erp_date, appoint_time = cls._split_appointment_datetime(
            lead_data.get("appointment_date", "")
        )
        if erp_date:
            payload["custom_appointment_date"] = erp_date
        if appoint_time:
            payload["custom_appoint"] = appoint_time
        # NOTE: Do NOT write to the `notes` child table — that field belongs to
        # the ERPNext website contact form ("Request Consultation") and must not
        # be overwritten by BizBot. AI notes are stored in custom_bot_service only.

        payload = {k: v for k, v in payload.items() if (v is not None and v != "") or k in ("salutation",)}
        headers = cls._get_headers()

        logger.debug(f"ERPNext create payload: {json.dumps(payload, default=str)}")

        async with httpx.AsyncClient(timeout=15, verify=settings.ERPNEXT_SSL_VERIFY) as client:
            try:
                resp = await client.post(
                    f"{settings.ERPNEXT_URL}/api/resource/Lead",
                    headers=headers,
                    json=payload,
                )
                resp.raise_for_status()
                logger.debug(f"ERPNext lead creation success: {resp.status_code}")
                return {"success": resp.status_code in (200, 201), "detail": resp.text}
            except httpx.HTTPStatusError as e:
                error_body = e.response.text
                status_code = e.response.status_code

                # Automatic Fallback: If ERPNext fails on Country link validation, retry without country
                if "Could not find Country" in error_body and payload.get("country"):
                    logger.warning(
                        f"Country '{payload.get('country')}' not found in ERPNext Country table. "
                        f"Retrying lead creation without country field..."
                    )
                    payload_no_country = {k: v for k, v in payload.items() if k != "country"}
                    try:
                        resp_retry = await client.post(
                            f"{settings.ERPNEXT_URL}/api/resource/Lead",
                            headers=headers,
                            json=payload_no_country,
                        )
                        if resp_retry.status_code in (200, 201):
                            logger.info("ERPNext lead created successfully on country-fallback retry.")
                            return {"success": True, "detail": resp_retry.text}
                    except Exception as retry_err:
                        logger.error(f"Fallback lead creation also failed: {retry_err}")

                logger.error(
                    f"[ERPNEXT-CREATE] Lead creation FAILED. "
                    f"Status: {status_code}, Response: {error_body}, "
                    f"Payload sent: {json.dumps(payload, default=str)}"
                )

                if 400 <= status_code < 500:
                    return {"success": False, "detail": error_body, "status_code": status_code}
                raise
            except Exception as e:
                logger.error(f"[ERPNEXT-CREATE] Lead creation error: {e}")
                raise

    @classmethod
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception(is_retryable_error),
        reraise=True,
    )
    async def update_lead(cls, lead_id: str, lead_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update an existing Lead in ERPNext."""
        full_notes = lead_data.get("notes", "")
        headers = cls._get_headers()


        existing_custom_bot_service = ""
        existing_status = ""
        existing_salutation = ""
        existing_first_name = ""
        existing_email_id = ""
        existing_company_name = ""
        existing_mobile_no = ""
        existing_phone = ""
        existing_country = ""
        async with httpx.AsyncClient(timeout=10, verify=settings.ERPNEXT_SSL_VERIFY) as client:
            try:
                resp = await client.get(
                    f"{settings.ERPNEXT_URL}/api/resource/Lead/{lead_id}",
                    headers=headers,
                )
                if resp.status_code == 200:
                    lead_details = resp.json().get("data", {})
                    existing_custom_bot_service = lead_details.get("custom_bot_service") or ""
                    existing_status = lead_details.get("status") or ""
                    existing_salutation = lead_details.get("salutation") or ""
                    existing_first_name = lead_details.get("first_name") or ""
                    existing_email_id = lead_details.get("email_id") or ""
                    existing_company_name = lead_details.get("company_name") or ""
                    existing_mobile_no = lead_details.get("mobile_no") or ""
                    existing_phone = lead_details.get("phone") or ""
                    existing_country = lead_details.get("country") or ""
            except Exception as e:
                logger.error(f"Error fetching existing lead details for {lead_id} from ERPNext: {e}")

        # Sanitize stale field values that may cause ERPNext validation errors
        VALID_STATUSES = {"Lead", "Open", "Replied", "Opportunity", "Quotation",
                          "Lost Quotation", "Interested", "Converted", "Do Not Contact"}
        VALID_SALUTATIONS = {"Mr", "Ms", "Mx", "Dr", "Mrs", "Madam", "Miss", "Master", "Prof"}

        sanitized_status = existing_status if existing_status in VALID_STATUSES else "Lead"
        sanitized_salutation = existing_salutation if existing_salutation in VALID_SALUTATIONS else ""

        if existing_status and existing_status not in VALID_STATUSES:
            logger.warning(f"Sanitized invalid lead status '{existing_status}' -> 'Lead' for {lead_id}")
        if existing_salutation and existing_salutation not in VALID_SALUTATIONS:
            logger.warning(f"Sanitized invalid salutation '{existing_salutation}' -> '' for {lead_id}")

       
        if full_notes:
            clean_note = full_notes.strip()
            existing_plain = cls._extract_plain_notes(existing_custom_bot_service)
            if clean_note and clean_note not in existing_plain:
                new_entry = cls._format_bot_service_entry(full_notes)
                if existing_custom_bot_service:
                    updated_custom_bot_service = f"{existing_custom_bot_service}\n{new_entry}"
                else:
                    updated_custom_bot_service = new_entry
            else:
                updated_custom_bot_service = existing_custom_bot_service
        else:
            updated_custom_bot_service = existing_custom_bot_service

        # Use BizBot's data if available, otherwise preserve existing ERPNext values
        new_first_name = lead_data.get("lead_name", "").strip()
        new_email = lead_data.get("email", "").strip()
        new_company = lead_data.get("company_name", "").strip()
        raw_country = lead_data.get("country", "")
        norm_country = normalize_country(raw_country)

        payload = {
            "doctype": "Lead",
            "salutation": sanitized_salutation,
            "status": sanitized_status,
            "first_name": new_first_name or existing_first_name,
            "email_id": new_email or existing_email_id,
            "company_name": new_company or existing_company_name,
            "country": norm_country or existing_country,
            "custom_bot_service": updated_custom_bot_service,
            "source": "Bot",
        }

        phone_no = lead_data.get("phone", "").strip()
        if phone_no:
            payload["mobile_no"] = phone_no
            payload["phone"] = phone_no
        elif existing_mobile_no or existing_phone:
            # Preserve existing phone data if BizBot doesn't have new phone
            if existing_mobile_no:
                payload["mobile_no"] = existing_mobile_no
            if existing_phone:
                payload["phone"] = existing_phone

        erp_date, appoint_time = cls._split_appointment_datetime(
            lead_data.get("appointment_date", "")
        )
        if erp_date:
            payload["custom_appointment_date"] = erp_date
        if appoint_time:
            payload["custom_appoint"] = appoint_time
        # NOTE: Do NOT write to the `notes` child table — that field belongs to
        # the ERPNext website contact form ("Request Consultation") and must not
        # be overwritten by BizBot. AI notes are stored in custom_bot_service only.

        payload = {k: v for k, v in payload.items() if (v is not None and v != "") or k in ("salutation", "status")}

        async with httpx.AsyncClient(timeout=15, verify=settings.ERPNEXT_SSL_VERIFY) as client:
            try:
                resp = await client.put(
                    f"{settings.ERPNEXT_URL}/api/resource/Lead/{lead_id}",
                    headers=headers,
                    json=payload,
                )
                resp.raise_for_status()
                logger.debug(f"ERPNext lead update success: {resp.status_code}")
                return {"success": resp.status_code == 200, "detail": resp.text}
            except httpx.HTTPStatusError as e:
                error_body = e.response.text
                status_code = e.response.status_code
                # Automatic Fallback: If ERPNext fails on Country link validation, retry without country
                if "Could not find Country" in error_body and payload.get("country"):
                    logger.warning(
                        f"Country '{payload.get('country')}' not found in ERPNext during update. "
                        f"Retrying lead update without country field..."
                    )
                    payload_no_country = {k: v for k, v in payload.items() if k != "country"}
                    try:
                        resp_retry = await client.put(
                            f"{settings.ERPNEXT_URL}/api/resource/Lead/{lead_id}",
                            headers=headers,
                            json=payload_no_country,
                        )
                        if resp_retry.status_code == 200:
                            logger.info("ERPNext lead updated successfully on country-fallback retry.")
                            return {"success": True, "detail": resp_retry.text}
                    except Exception as retry_err:
                        logger.error(f"Fallback lead update also failed: {retry_err}")

                logger.error(f"ERPNext lead update failed with {status_code}: {error_body}")
                raise
            except Exception as e:
                logger.error(f"ERPNext lead update error: {e}")
                raise

    @classmethod
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception(is_retryable_error),
        reraise=True,
    )
    async def delete_lead(cls, lead_id: str) -> Dict[str, Any]:
        """Delete a Lead from ERPNext."""
        headers = cls._get_headers()
        async with httpx.AsyncClient(timeout=15, verify=settings.ERPNEXT_SSL_VERIFY) as client:
            try:
                resp = await client.delete(
                    f"{settings.ERPNEXT_URL}/api/resource/Lead/{lead_id}",
                    headers=headers,
                )
                resp.raise_for_status()
                logger.debug(f"ERPNext lead deletion success: {resp.status_code}")
                return {"success": resp.status_code in (200, 202, 204), "detail": resp.text}
            except httpx.HTTPStatusError as e:
                logger.error(f"ERPNext lead deletion failed with {e.response.status_code}: {e.response.text}")
                raise
            except Exception as e:
                logger.error(f"ERPNext lead deletion error: {e}")
                raise


