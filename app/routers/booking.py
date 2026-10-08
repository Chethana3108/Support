import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings
from app.database import get_db
from app.models import LeadState, Conversation, Message
from app.schemas import BookingSlotResponse, BookingRequest, BookingResponse
from app.services.outlook import OutlookService
from app.services.lead import LeadService

logger = logging.getLogger("biztechbot")

router = APIRouter(prefix="/api/booking", tags=["Booking"])

# ─── Email Validation for Booking ───

FAKE_EMAIL_PLACEHOLDERS = {
    "client@example.com", "you@company.com", "test@test.com",
    "user@example.com", "example@example.com"
}


def is_valid_booking_email(email: Optional[str]) -> tuple[bool, str]:
    """Validate that an email is present, real (not a placeholder), and properly formatted.
    
    Returns:
        Tuple of (is_valid, error_reason). If is_valid is True, error_reason is empty.
    """
    if not email or not email.strip():
        return False, "missing"
    
    email_clean = email.strip().lower()
    
    if email_clean in FAKE_EMAIL_PLACEHOLDERS:
        return False, "missing"
    
    if "@" not in email_clean or "." not in email_clean.split("@")[-1]:
        return False, "invalid"
    
    return True, ""


@router.get("/slots")
async def get_available_slots(
    date: str = Query(..., description="Date in YYYY-MM-DD format")
):
    """
    Get available booking slots for a selected date.
    Applies:
    1. Rule 1: 24-hour delay rule (no booking within next 24 hours).
    2. Rule 2: Outlook Calendar busy slot removal via Microsoft Graph API for chethana@biztechnosys.com.
    """
    result = await OutlookService.get_available_slots(date)
    return result


@router.get("/verify-email")
async def verify_email(
    email: str = Query(..., description="Email address to verify")
):
    """
    Verify whether an email address actually exists (has a real mailbox).
    - For @biztechnosys.com: checks Azure AD via MS Graph API.
    - For external domains: DNS MX lookup + SMTP RCPT TO verification.
    Returns: {"exists": bool, "reason": str, "method": str}
    """
    result = await OutlookService.verify_email_exists(email)
    return result


@router.post("/book", response_model=BookingResponse)
async def book_appointment(
    req: BookingRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Book a Discovery Call appointment.
    1. Validates session lead state and email address.
    2. Schedules meeting on Outlook Calendar (chethana@biztechnosys.com).
    3. Sends confirmation email to user & chethana@biztechnosys.com.
    4. Updates DB and syncs lead appointment date in ERPNext.
    """
    stmt = select(LeadState).where(LeadState.conversation_id == req.session_id)
    res = await db.execute(stmt)
    lead_state = res.scalar_one_or_none()

    if not lead_state:
        raise HTTPException(status_code=404, detail="Session or lead state not found.")

    # VALIDATE EMAIL before creating appointment
    email_valid, email_reason = is_valid_booking_email(lead_state.email)
    if not email_valid:
        detail_msg = (
            "Cannot book appointment without a valid email address. "
            "Please provide your email first."
        )
        logger.warning(
            f"Booking blocked for session {req.session_id}: email validation failed "
            f"(reason={email_reason}, email='{lead_state.email}')"
        )
        raise HTTPException(status_code=400, detail=detail_msg)

    lead_name = lead_state.lead_name or ""
    user_email = lead_state.email
    company_name = lead_state.company_name or ""
    notes = lead_state.notes or ""

    formatted_appointment = f"{req.appointment_date} {req.appointment_time}"
    lead_state.appointment_date = formatted_appointment

    # Schedule MS Graph Outlook Calendar Event
    calendar_success = await OutlookService.create_calendar_event(
        lead_name=lead_name,
        user_email=user_email,
        company_name=company_name,
        date_str=req.appointment_date,
        time_str=req.appointment_time,
        notes=notes
    )

    # Send MS Graph Email confirmation
    email_success = await OutlookService.send_confirmation_emails(
        lead_name=lead_name,
        user_email=user_email,
        company_name=company_name,
        date_str=req.appointment_date,
        time_str=req.appointment_time,
        notes=notes
    )

    # Save to DB and sync to ERPNext
    await db.commit()
    try:
        await LeadService.sync_lead_to_erpnext(db, lead_state, newly_filled=True)
    except Exception as e:
        logger.error(f"Error syncing appointment date to ERPNext: {e}")

    # Add confirmation message to conversation thread
    confirmation_msg = (
        f"🎉 **Discovery Call Confirmed!**\n\n"
        f"Your appointment with the Biztechnosys team is scheduled for **{req.appointment_date} at {req.appointment_time}** (IST).\n\n"
        f"A calendar invitation and confirmation email have been sent to **{user_email}** and **{settings.OUTLOOK_EMAIL}**.\n\n"
        f"**Do you have any other project or requirement we can help you with?**"
    )

    msg = Message(
        conversation_id=req.session_id,
        role="assistant",
        content=confirmation_msg
    )
    db.add(msg)
    await db.commit()

    return BookingResponse(
        success=True,
        message=f"Appointment successfully booked for {req.appointment_date} at {req.appointment_time}",
        appointment_date=req.appointment_date,
        appointment_time=req.appointment_time
    )
