import logging
import json
from typing import Dict, Any, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import LeadState, Conversation
from app.services.erpnext import ERPNextService

logger = logging.getLogger("biztechbot")

class LeadService:
    @staticmethod
    async def get_or_create_lead_state(
        db: AsyncSession, 
        conversation_id: str, 
        user_id: str
    ) -> LeadState:
        """
        Fetch lead state for a conversation or create it.
        If a new conversation is started, check if there is an existing lead state
        for this user from previous conversations, and clone it to preserve state.
        """
        stmt = select(LeadState).where(LeadState.conversation_id == conversation_id)
        result = await db.execute(stmt)
        lead_state = result.scalar_one_or_none()

        if not lead_state:
            # Look up most recent lead state across all conversations of the user
            stmt_prev = (
                select(LeadState)
                .join(Conversation, LeadState.conversation_id == Conversation.conversation_id)
                .where(Conversation.user_id == user_id)
                .order_by(Conversation.created_at.desc())
                .limit(1)
            )
            result_prev = await db.execute(stmt_prev)
            prev_lead = result_prev.scalar_one_or_none()
            
            if prev_lead:
                logger.info(f"Pre-populating lead state from previous conversation for user: {user_id}")
                lead_state = LeadState(
                    conversation_id=conversation_id,
                    lead_name=prev_lead.lead_name,
                    company_name=prev_lead.company_name,
                    email=prev_lead.email,
                    phone=prev_lead.phone,
                    notes=prev_lead.notes,
                    lead_saved=prev_lead.lead_saved,
                    lead_id=prev_lead.lead_id
                )
            else:
                lead_state = LeadState(
                    conversation_id=conversation_id,
                    lead_name="",
                    company_name="",
                    email="",
                    phone="",
                    notes=""
                )
            
            db.add(lead_state)
            await db.commit()
            logger.debug(f"Initialized lead state for conversation: {conversation_id}")
        
        return lead_state

    @classmethod
    async def update_lead_state(
        cls, 
        db: AsyncSession, 
        conversation_id: str,
        user_id: str,
        new_data: Dict[str, Any]
    ) -> tuple[LeadState, Dict[str, Any]]:
        """
        Merge new lead data into database.
        Returns the updated LeadState model and a dict of fields that were newly filled.
        """
        lead_state = await cls.get_or_create_lead_state(db, conversation_id, user_id)
        newly_filled = {}

        for key in ["lead_name", "company_name", "email", "phone", "notes"]:
            val = new_data.get(key, "")
            if isinstance(val, str):
                val = val.strip()
            
            if val:
                existing_val = getattr(lead_state, key)
                if not existing_val:
                    setattr(lead_state, key, val)
                    newly_filled[key] = val
                elif existing_val != val:
                    setattr(lead_state, key, val)
                    newly_filled[key] = val
        
        if newly_filled:
            await db.commit()
            logger.info(f"Updated lead state fields {list(newly_filled.keys())} for session {conversation_id}")
        
        return lead_state, newly_filled

    @classmethod
    async def sync_lead_to_erpnext(
        cls, 
        db: AsyncSession, 
        lead_state: LeadState, 
        newly_filled: bool
    ) -> bool:
        """
        Orchestrates lead creation or update on ERPNext.
        Returns True if a sync operation (create/update) successfully occurred during this step.
        
        Flow:
        - As soon as lead_name AND company_name are collected, CREATE the lead in ERPNext
          (even without email/phone).
        - If the lead is already saved and new fields (email, phone, etc.) are collected later,
          UPDATE the existing lead in ERPNext.
        """
        # Minimum fields required for lead creation in ERPNext
        has_mandatory = bool(lead_state.lead_name and lead_state.company_name)

        logger.info(
            f"[LEAD-SYNC] Entry: lead_name='{lead_state.lead_name}', "
            f"company_name='{lead_state.company_name}', email='{lead_state.email}', "
            f"phone='{lead_state.phone}', lead_saved={lead_state.lead_saved}, "
            f"lead_id='{lead_state.lead_id}', has_mandatory={has_mandatory}, "
            f"newly_filled={newly_filled}"
        )

        lead_dict = {
            "lead_name": lead_state.lead_name,
            "company_name": lead_state.company_name,
            "email": lead_state.email,
            "phone": lead_state.phone,
            "notes": lead_state.notes,
        }

        # ── PATH 1: Lead NOT yet saved, but we have name + company → CREATE ──
        if not lead_state.lead_saved and has_mandatory:
            email = lead_state.email
            logger.info(
                f"[LEAD-SYNC] CREATE PATH: Attempting to create/find lead for session "
                f"{lead_state.conversation_id} (name: {lead_state.lead_name}, "
                f"company: {lead_state.company_name}, email: {email or '(not yet collected)'})"
            )
            
            # Try to find existing lead: first by email, then by name+company
            existing_lead_id = None
            try:
                if email:
                    existing_lead_id = await ERPNextService.get_lead_by_email(email)
                    logger.info(f"[LEAD-SYNC] Search by email '{email}' result: {existing_lead_id}")
                if not existing_lead_id:
                    existing_lead_id = await ERPNextService.get_lead_by_name_and_company(
                        lead_state.lead_name, lead_state.company_name
                    )
                    logger.info(f"[LEAD-SYNC] Search by name+company result: {existing_lead_id}")
            except Exception as e:
                logger.error(f"[LEAD-SYNC] Error checking existing lead: {e}", exc_info=True)

            if existing_lead_id:
                lead_state.lead_id = existing_lead_id
                lead_state.lead_saved = True
                await db.commit()
                logger.info(f"[LEAD-SYNC] Found existing lead, marked lead_saved=True, ID: {existing_lead_id}")
                
                try:
                    result = await ERPNextService.update_lead(existing_lead_id, lead_dict)
                    if result.get("success"):
                        logger.info(f"[LEAD-SYNC] [OK] Associated and updated existing ERPNext Lead ID: {existing_lead_id}")
                        return True
                    else:
                        logger.error(f"[LEAD-SYNC] update_lead returned unsuccessful: {result}")
                except Exception as e:
                    logger.error(f"[LEAD-SYNC] Failed to update associated lead {existing_lead_id}: {e}", exc_info=True)
            else:
                logger.info(
                    f"[LEAD-SYNC] No existing lead found. Creating new lead with: "
                    f"name='{lead_state.lead_name}', company='{lead_state.company_name}', "
                    f"email='{lead_state.email or '(empty)'}', phone='{lead_state.phone or '(empty)'}'"
                )
                try:
                    result = await ERPNextService.create_lead(lead_dict)
                    logger.info(f"[LEAD-SYNC] create_lead result: {result}")
                    if result.get("success"):
                        lead_state.lead_saved = True
                        try:
                            res_json = json.loads(result["detail"])
                            lead_state.lead_id = res_json["data"]["name"]
                            logger.info(f"[LEAD-SYNC] Parsed new lead ID: {lead_state.lead_id}")
                        except Exception as parse_err:
                            logger.warning(f"[LEAD-SYNC] Could not parse created ERPNext lead ID: {parse_err}")
                        await db.commit()
                        logger.info(f"[LEAD-SYNC] [OK] Created new ERPNext Lead. Session: {lead_state.conversation_id}, lead_saved={lead_state.lead_saved}")
                        return True
                    else:
                        # ERPNext returned a 4xx error (e.g., validation failure)
                        # Log the full detail so we can diagnose
                        status = result.get("status_code", "unknown")
                        detail = result.get("detail", "no detail")
                        logger.error(
                            f"[LEAD-SYNC] ERPNext REJECTED lead creation (HTTP {status}). "
                            f"Detail: {detail}. "
                            f"This may mean ERPNext requires additional mandatory fields "
                            f"(e.g., email). Lead will be retried on the next turn when "
                            f"more fields are collected."
                        )
                except Exception as e:
                    logger.error(f"[LEAD-SYNC] Failed to create new lead: {e}", exc_info=True)

        # ── PATH 2: Lead ALREADY saved → UPDATE with newly collected fields ──
        elif lead_state.lead_saved and lead_state.lead_id and newly_filled:
            logger.info(f"[LEAD-SYNC] UPDATE PATH: Updating lead ID {lead_state.lead_id} with new fields")
            try:
                result = await ERPNextService.update_lead(lead_state.lead_id, lead_dict)
                if result.get("success"):
                    logger.info(f"[LEAD-SYNC] [OK] Updated existing ERPNext Lead ID: {lead_state.lead_id}")
                    return True
                else:
                    logger.error(f"[LEAD-SYNC] update_lead returned unsuccessful: {result}")
            except Exception as e:
                logger.error(f"[LEAD-SYNC] Failed to update lead {lead_state.lead_id}: {e}", exc_info=True)
        else:
            # Log why we're skipping sync
            reasons = []
            if lead_state.lead_saved and not newly_filled:
                reasons.append("lead already saved and no newly filled fields")
            if not has_mandatory:
                missing = []
                if not lead_state.lead_name:
                    missing.append("lead_name")
                if not lead_state.company_name:
                    missing.append("company_name")
                reasons.append(f"missing mandatory fields: {missing}")
            logger.info(f"[LEAD-SYNC] SKIP: No sync action taken. Reason: {'; '.join(reasons)}")

        return False
