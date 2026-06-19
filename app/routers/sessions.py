import logging
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from app.database import get_db
from app.models import Conversation, Message, LeadState
from app.schemas import LeadStateSchema

logger = logging.getLogger("biztechbot")

router = APIRouter(prefix="/api/sessions", tags=["Sessions"])

@router.get("/{session_id}")
async def get_session_info(session_id: str, db: AsyncSession = Depends(get_db)):
    """Retrieve details for a specific session: history, lead state, save status."""
    # Fetch the conversation along with its messages and lead state
    stmt = select(Conversation).where(Conversation.conversation_id == session_id)
    result = await db.execute(stmt)
    conversation = result.scalar_one_or_none()

    if not conversation:
        raise HTTPException(status_code=404, detail="Session not found.")

    # Fetch messages (ordered chronologically)
    stmt_msgs = select(Message).where(Message.conversation_id == session_id).order_by(Message.created_at)
    result_msgs = await db.execute(stmt_msgs)
    messages = result_msgs.scalars().all()

    # Fetch lead state
    stmt_lead = select(LeadState).where(LeadState.conversation_id == session_id)
    result_lead = await db.execute(stmt_lead)
    lead_state = result_lead.scalar_one_or_none()

    lead_data = {
        "lead_name": "",
        "company_name": "",
        "email": "",
        "phone": "",
        "notes": "",
    }
    lead_saved = False

    if lead_state:
        lead_data = {
            "lead_name": lead_state.lead_name,
            "company_name": lead_state.company_name,
            "email": lead_state.email,
            "phone": lead_state.phone,
            "notes": lead_state.notes,
        }
        lead_saved = lead_state.lead_saved

    formatted_messages = [
        {"role": msg.role, "content": msg.content, "created_at": msg.created_at.isoformat()}
        for msg in messages
    ]

    return {
        "session_id": session_id,
        "user_id": conversation.user_id,
        "message_count": len(messages),
        "lead": lead_data,
        "lead_saved": lead_saved,
        "messages": formatted_messages,
    }

@router.delete("/{session_id}")
async def delete_session(session_id: str, db: AsyncSession = Depends(get_db)):
    """Delete a session. Cascades will delete messages, lead state, and embeddings."""
    stmt = select(Conversation).where(Conversation.conversation_id == session_id)
    result = await db.execute(stmt)
    conversation = result.scalar_one_or_none()

    if not conversation:
        raise HTTPException(status_code=404, detail="Session not found.")

    await db.execute(delete(Conversation).where(Conversation.conversation_id == session_id))
    await db.commit()
    
    logger.info(f"Deleted session: {session_id}")
    return {"status": "deleted"}
