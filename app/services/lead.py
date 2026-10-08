import logging
import json
import httpx
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
                logger.debug(f"Pre-populating lead state from previous conversation for user: {user_id}")
                lead_state = LeadState(
                    conversation_id=conversation_id,
                    lead_name=prev_lead.lead_name,
                    company_name=prev_lead.company_name,
                    email=prev_lead.email,
                    appointment_date="",
                    phone=prev_lead.phone,
                    country=prev_lead.country,
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
                    appointment_date="",
                    phone="",
                    country="",
                    notes=""
                )
            
            db.add(lead_state)
            await db.commit()
            logger.debug(f"Initialized lead state for conversation: {conversation_id}")
        
        return lead_state

    @classmethod
    async def extract_new_requirement_part(cls, old_requirement: str, new_requirement: str) -> str:
        """
        Use DeepSeek to extract only the new/changed requirement part, removing
        any parts that are just repeating the old requirement.
        If the new requirement is completely different, it will return the new requirement.
        """
        if not old_requirement:
            return new_requirement.strip()
        
        old_clean = old_requirement.strip()
        new_clean = new_requirement.strip()
        if old_clean == new_clean:
            return ""

        prompt = f"""
        Given an old requirement summary and a new requirement summary, extract ONLY the new requirement details that have been added or changed in the new summary.
        Do not include details that are already present in the old requirement.
        If the new summary is completely different, return the new summary.

        Old Requirement: {old_clean}
        New Requirement: {new_clean}

        Respond with ONLY the newly added or changed requirement text. Do not include any introductory text or explanation.
        """
        try:
            from app.services.llm import call_deepseek
            reply = await call_deepseek([
                {"role": "system", "content": "You are a precise text extraction assistant. Output only the requested new requirement text."},
                {"role": "user", "content": prompt}
            ])
            extracted = reply.strip()
            logger.debug(f"Extracted new requirement part: '{old_clean}' vs '{new_clean}' -> '{extracted}'")
            return extracted
        except Exception as e:
            logger.error(f"Error extracting new requirement part: {e}")
            return new_clean

    @classmethod
    async def has_requirement_topic_changed(cls, old_requirement: str, new_requirement: str) -> bool:
        """
        Use DeepSeek to determine if the user's requirement/topic has actually changed to a new subject,
        as opposed to being a minor rephrasing, refinement, or elaboration of the same requirement.
        """
        if not old_requirement or not new_requirement:
            return False
        
        old_clean = old_requirement.strip()
        new_clean = new_requirement.strip()
        if old_clean == new_clean:
            return False

        prompt = f"""
        Compare the following two user requirement summaries for a customer support / services bot.
        Determine if the requirement has changed to a DIFFERENT topic/project requirement (e.g. from "knowledge bot" to "data migration", or from "React development" to "Sitecore upgrade").
        If the new requirement is just a minor rephrasing, refinement, elaboration, or detail addition of the old requirement (e.g. "knowledge bot" vs "knowledge bot for customer support on Sitecore"), it has NOT changed.

        Old Requirement: {old_clean}
        New Requirement: {new_clean}

        Respond with exactly 'YES' if the requirement has changed to a different topic, and 'NO' if it is a minor rephrasing, refinement, elaboration, or has not changed.
        Respond with ONLY 'YES' or 'NO'. No other text.
        """
        try:
            from app.services.llm import call_deepseek
            reply = await call_deepseek([
                {"role": "system", "content": "You are a precise classification assistant. Respond with ONLY 'YES' or 'NO'."},
                {"role": "user", "content": prompt}
            ])
            result = reply.strip().upper()
            logger.debug(f"Requirement topic change detection: '{old_clean}' vs '{new_clean}' -> {result}")
            return "YES" in result
        except Exception as e:
            logger.error(f"Error checking if requirement topic changed: {e}")
            return False

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

        for key in ["lead_name", "company_name", "email", "phone", "country", "notes"]:
            val = new_data.get(key, "")
            if isinstance(val, str):
                val = val.strip()
            
            if val:
                existing_val = getattr(lead_state, key)
                if isinstance(existing_val, str):
                    existing_val = existing_val.strip()
                
                if not existing_val:
                    if key == "notes":
                        # Only set notes if the lead has both name and company name (is ready)
                        is_ready_for_notes = bool(lead_state.lead_name) and bool(lead_state.company_name)
                        if not is_ready_for_notes:
                            continue
                    setattr(lead_state, key, val)
                    newly_filled[key] = val
                elif existing_val != val:
                    if key == "notes":
                        # Check if the notes topic actually changed.
                        # If it is just a minor rephrasing/elaboration, do not overwrite/update.
                        if not await cls.has_requirement_topic_changed(existing_val, val):
                            continue
                        # Extract only the newly added/changed requirement part
                        val = await cls.extract_new_requirement_part(existing_val, val)
                        if not val:
                            continue
                    setattr(lead_state, key, val)
                    newly_filled[key] = val
        
        if newly_filled:
            await db.commit()
            logger.debug(f"Updated lead fields {list(newly_filled.keys())} for session {conversation_id}")
        
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
        # ONLY sync to ERPNext if an appointment is actually booked!
        # Leads without an appointment are kept in local DB only and never stored in ERPNext.
        if not lead_state.appointment_date or not lead_state.appointment_date.strip():
            logger.debug(
                f"Skipping ERPNext sync for session {lead_state.conversation_id}: "
                f"No appointment booked yet. Only leads with confirmed appointments are saved to ERPNext."
            )
            return False

        has_mandatory = bool(lead_state.appointment_date and (lead_state.lead_name or lead_state.email))

        logger.debug(
            f"Lead sync check: name='{lead_state.lead_name}', "
            f"company='{lead_state.company_name}', appointment='{lead_state.appointment_date}', "
            f"saved={lead_state.lead_saved}, lead_id='{lead_state.lead_id}'"
        )

        lead_name_val = (lead_state.lead_name or "").strip()
        if not lead_name_val and lead_state.email:
            lead_name_val = lead_state.email.split("@")[0].capitalize()

        lead_dict = {
            "lead_name": lead_name_val or "Website Lead",
            "company_name": lead_state.company_name,
            "email": lead_state.email,
            "appointment_date": lead_state.appointment_date,
            "phone": lead_state.phone,
            "country": lead_state.country,
            "notes": lead_state.notes,
        }

        # ── PATH 1: Lead NOT yet saved, but we have name + company → CREATE ──
        if not lead_state.lead_saved and has_mandatory:
            logger.debug(
                f"Attempting to create/find lead for session {lead_state.conversation_id}"
            )
            
            # Try to find existing lead: email → phone → name+company
            existing_lead_id = None
            try:
                if lead_state.email:
                    existing_lead_id = await ERPNextService.get_lead_by_email(lead_state.email)
                    logger.debug(f"Search by email result: {existing_lead_id}")
                if not existing_lead_id and lead_state.phone:
                    existing_lead_id = await ERPNextService.get_lead_by_phone(lead_state.phone)
                    logger.debug(f"Search by phone result: {existing_lead_id}")
                if not existing_lead_id:
                    existing_lead_id = await ERPNextService.get_lead_by_name_and_company(
                        lead_state.lead_name, lead_state.company_name
                    )
                    logger.debug(f"Search by name+company result: {existing_lead_id}")
            except Exception as e:
                logger.error(f"Error checking existing lead: {e}", exc_info=True)

            if existing_lead_id:
                lead_state.lead_id = existing_lead_id
                lead_state.lead_saved = True
                await db.commit()
                
                try:
                    result = await ERPNextService.update_lead(existing_lead_id, lead_dict)
                    if result.get("success"):
                        logger.info(f"Lead updated (existing): {existing_lead_id}")
                        return True
                    else:
                        logger.error(f"Failed to update existing lead {existing_lead_id}: {result}")
                except Exception as e:
                    logger.error(f"Failed to update existing lead {existing_lead_id}: {e}", exc_info=True)
            else:
                logger.debug("No existing lead found, creating new lead")
                try:
                    result = await ERPNextService.create_lead(lead_dict)
                    if result.get("success"):
                        lead_state.lead_saved = True
                        try:
                            res_json = json.loads(result["detail"])
                            lead_state.lead_id = res_json["data"]["name"]
                        except Exception as parse_err:
                            logger.warning(f"Could not parse created ERPNext lead ID: {parse_err}")
                        await db.commit()
                        logger.info(f"Lead created: {lead_state.lead_id}")
                        return True
                    else:
                        # ERPNext returned a 4xx error (e.g., validation failure)
                        status = result.get("status_code", "unknown")
                        detail = result.get("detail", "no detail")
                        logger.error(
                            f"ERPNext rejected lead creation (HTTP {status}): {detail}"
                        )
                except Exception as e:
                    logger.error(f"Failed to create new lead: {e}", exc_info=True)

        # ── PATH 2: Lead ALREADY saved → UPDATE with newly collected fields ──
        elif lead_state.lead_saved and lead_state.lead_id and newly_filled:
            try:
                # If email or phone is set, check if they belong to an existing lead in ERPNext
                # that is different from our current lead_state.lead_id
                target_lead_id = lead_state.lead_id
                if lead_state.email or lead_state.phone:
                    existing_lead_id = None
                    if lead_state.email:
                        existing_lead_id = await ERPNextService.get_lead_by_email(lead_state.email)
                    if not existing_lead_id and lead_state.phone:
                        existing_lead_id = await ERPNextService.get_lead_by_phone(lead_state.phone)
                    
                    if existing_lead_id and existing_lead_id != lead_state.lead_id:
                        logger.info(
                            f"Late identity match found in ERPNext: "
                            f"switching lead ID {lead_state.lead_id} -> {existing_lead_id}. "
                            f"Deleting temporary duplicate lead."
                        )
                        # Deleting the temporary duplicate lead from ERPNext
                        temp_lead_id = lead_state.lead_id
                        try:
                            await ERPNextService.delete_lead(temp_lead_id)
                            logger.info(f"Deleted temporary duplicate lead {temp_lead_id} from ERPNext")
                        except Exception as del_err:
                            logger.warning(f"Failed to delete temporary duplicate lead {temp_lead_id}: {del_err}")

                        # Update our local database to point to the correct lead_id
                        lead_state.lead_id = existing_lead_id
                        await db.commit()
                        target_lead_id = existing_lead_id

                result = await ERPNextService.update_lead(target_lead_id, lead_dict)
                if result.get("success"):
                    logger.info(f"Lead updated: {target_lead_id}")
                    return True
                else:
                    logger.error(f"Failed to update lead {target_lead_id}: {result}")
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    # Lead was deleted from ERPNext — reset and create a new one
                    logger.warning(
                        f"Lead {target_lead_id} no longer exists in ERPNext (404). "
                        f"Resetting local state and creating a new lead."
                    )
                    lead_state.lead_saved = False
                    lead_state.lead_id = None
                    await db.commit()
                    try:
                        result = await ERPNextService.create_lead(lead_dict)
                        if result.get("success"):
                            lead_state.lead_saved = True
                            try:
                                res_json = json.loads(result["detail"])
                                lead_state.lead_id = res_json["data"]["name"]
                            except Exception as parse_err:
                                logger.warning(f"Could not parse created ERPNext lead ID: {parse_err}")
                            await db.commit()
                            logger.info(f"Lead re-created after 404: {lead_state.lead_id}")
                            return True
                    except Exception as create_err:
                        logger.error(f"Failed to re-create lead after 404: {create_err}", exc_info=True)
                else:
                    logger.error(f"Failed to update lead {lead_state.lead_id}: {e}", exc_info=True)
            except Exception as e:
                logger.error(f"Failed to update lead {lead_state.lead_id}: {e}", exc_info=True)
        else:
            # Log why we're skipping sync
            if lead_state.lead_saved and not newly_filled:
                logger.debug("Lead sync skipped: already saved, no new fields")
            elif not has_mandatory:
                missing = []
                if not lead_state.lead_name:
                    missing.append("lead_name")
                if not lead_state.company_name:
                    missing.append("company_name")
                logger.debug(f"Lead sync skipped: missing {missing}")

        return False
