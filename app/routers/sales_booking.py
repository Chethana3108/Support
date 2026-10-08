import logging
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import LeadState
from app.schemas import SalesBookingSubmitRequest, SalesBookingSubmitResponse
from app.services.lead import LeadService
from app.services.memory import MemoryService

logger = logging.getLogger("biztechbot")

router = APIRouter(prefix="/api/sales-booking", tags=["Sales Booking"])


@router.post("/submit", response_model=SalesBookingSubmitResponse)
async def submit_sales_booking(
    req: SalesBookingSubmitRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Conversational Sales Booking — Step 1: Persist lead data.

    Called after the frontend has collected email, company name, and meeting
    topic through the step-by-step chat flow.  Creates or updates the
    LeadState row and syncs to ERPNext so the lead is captured even if the
    user abandons before picking a calendar slot.
    """
    try:
        # Ensure User + Conversation records exist in DB first
        # (The sales booking flow bypasses the normal /api/chat endpoint
        #  which normally creates these, so we must do it here.)
        await MemoryService.create_conversation_if_not_exists(
            db, req.session_id, req.user_id
        )

        lead_data = {
            "lead_name": req.lead_name or "",
            "email": req.email,
            "company_name": req.company_name,
            "notes": req.meeting_topic,
        }

        # Upsert local LeadState
        lead_state, newly_filled = await LeadService.update_lead_state(
            db, req.session_id, req.user_id, lead_data
        )

        # Sync to ERPNext
        try:
            await LeadService.sync_lead_to_erpnext(db, lead_state, bool(newly_filled))
        except Exception as e:
            logger.error(f"Error syncing sales-booking lead to ERPNext: {e}", exc_info=True)

        return SalesBookingSubmitResponse(
            success=True,
            lead_saved=lead_state.lead_saved,
            message="Lead captured successfully.",
        )
    except Exception as e:
        logger.error(f"Sales booking submit error: {e}", exc_info=True)
        return SalesBookingSubmitResponse(
            success=False,
            lead_saved=False,
            message=f"Error capturing lead: {str(e)}",
        )
