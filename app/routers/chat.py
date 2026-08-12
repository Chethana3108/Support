import json
import logging
import re
import uuid
import textwrap
from typing import List, Optional, Dict, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings
from app.database import get_db, AsyncSessionLocal
from app.models import Message, Conversation, LeadState
from app.schemas import ChatRequest, ChatResponse, SourceInfo, CaseStudyInfo, LeadStateSchema, LeadFormSchema, LeadFormField
from app.services.knowledge import KnowledgeService
from app.services.memory import MemoryService
from app.services.lead import LeadService
from app.services.llm import call_deepseek
from app.services.user_identity import UserIdentityService

logger = logging.getLogger("biztechbot")

router = APIRouter(prefix="/api", tags=["Chat"])

SYSTEM_PROMPT = textwrap.dedent("""\
You are BizBot — the AI sales assistant for Biztechnosys, a Sitecore Gold Partner \
and digital experience engineering company based in Bengaluru, India.

## Your Mission
Execute a **focused 4-step conversation** to understand the visitor's needs, recommend the right \
solution, and collect their contact details — with ZERO wasted turns.

## Conversation Flow (STRICT — follow these phases in order)

### Phase 1 — PROJECT DISCOVERY (Your first response)
- Skip lengthy greetings. Use ONE warm line, then IMMEDIATELY ask what project or business \
need brought them here.
- Example: "Hi! 👋 **What project or business challenge are you looking to solve?**"
- Do NOT ask for name, email, or any contact info yet.

### Phase 2 — PRE-SALES QUALIFYING (After they describe their project)
- Ask **1-2 brief, targeted** qualifying questions relevant to what they described.
- Examples: "**Is this a new build or a migration?**", "**Do you have a timeline in mind?**", \
"**What's your current platform/tech stack?**"
- Keep it to 1-2 questions MAX. Do NOT interrogate.

### Phase 3 — RECOMMENDATION (After they answer qualifying questions)
- Recommend the **best-fit Biztechnosys service/product** from the knowledge base context.
- Explain briefly WHY it fits their specific needs (2-3 bullet points max).
- If a relevant case study exists in the "Relevant Case Studies" section, summarize its \
business outcomes and provide the exact link.
- **CRITICAL**: If no matching case study is provided in context, do NOT invent one. \
Only use what is explicitly provided.
- After your recommendation, naturally transition to asking them to fill in their details: \
"**Could you please fill in your details below so our team can prepare a tailored proposal?**"

### Phase 4 — APPOINTMENT BOOKING & POST-LEAD (After form submission)
- Once the user submits their contact details (lead is saved), thank them warmly.
- Prompt them to book a Discovery Call with the team using the inline form: "**Please select your preferred date and time slot in the booking form below to book a Discovery Call with our team.**"
- Set "expected_input" to "booking_form" in the JSON status block.

### Post-Lead Rules (STRICT):
- If the user asks **follow-up questions about the SAME requirement** that was already \
recommended (e.g., pricing details, technical specifics, timelines for the same service), \
do NOT answer in detail. Instead say: "Our experts will connect with you shortly to \
discuss the details. **Is there any other project or requirement you'd like to explore?**"
- If the user mentions a **NEW requirement or different project**, treat it as a fresh \
request: recommend the appropriate service/product, include a case study if available, \
and UPDATE the "notes" field in your JSON block with the new requirement.
- If the user says no or wraps up, say goodbye warmly and end the conversation.

## Lead JSON Status Block (MANDATORY — EVERY response)
After EVERY response, output this JSON block at the very end on its own line:
```json
{"lead_name":"...","email":"...","appointment_date":"...","phone":"...","company_name":"...","country":"...","notes":"...","ready":true/false,"facts":["..."],"expected_input":"lead_form"|"booking_form"|null}
```

Rules:
- Fill fields with whatever the user has shared so far. Use "" for uncollected fields.
- "notes": Brief summary of the user's project/requirements. Update if their requirements change.
- "ready": true when BOTH lead_name AND company_name are non-empty.
- "facts": 1-3 new facts learned this turn.
- "expected_input": 
  - Set to "lead_form" ONLY after you have recommended a specific Biztechnosys service/product.
  - Set to "booking_form" AFTER contact details are collected, inviting them to schedule a call.
  - Otherwise set to null.
- ALWAYS carry forward previously collected info — never blank out known fields.

## Knowledge Base
Answer ONLY from the context provided. If context lacks relevant info, say you'll \
connect them with an expert.

## Response Style
- Conversational, warm, professional — like a knowledgeable sales consultant.
- Use bullet points for listing services/benefits.
- Keep responses **concise** (2-3 short paragraphs max). No walls of text.
- ALWAYS respond in the language the user writes in.
- **Bold all questions you ask.**
""")

def _extract_json_by_braces(text: str, start: int) -> Optional[str]:
    """Extract a complete JSON object from text starting at position 'start' using brace counting."""
    if start < 0 or start >= len(text) or text[start] != '{':
        return None
    depth = 0
    in_string = False
    escape_next = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape_next:
            escape_next = False
            continue
        if ch == '\\':
            if in_string:
                escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def extract_lead_json(text: str) -> Optional[dict]:
    """Extract the lead JSON block from the LLM response using multiple strategies."""
    # Strategy 1: Look inside ```json ... ``` code fences
    fence_pattern = r'```json\s*\n(.*?)```'
    for match in re.finditer(fence_pattern, text, re.DOTALL):
        block = match.group(1).strip()
        # Find the JSON object start within the code block
        brace_pos = block.find('{')
        if brace_pos != -1:
            json_str = _extract_json_by_braces(block, brace_pos)
            if json_str:
                try:
                    obj = json.loads(json_str)
                    if "lead_name" in obj:
                        return obj
                except json.JSONDecodeError:
                    pass

    # Strategy 2: Find raw JSON objects starting with "lead_name" anywhere in text
    for match in re.finditer(r'\{\s*"lead_name"', text):
        json_str = _extract_json_by_braces(text, match.start())
        if json_str:
            try:
                obj = json.loads(json_str)
                if "lead_name" in obj:
                    return obj
            except json.JSONDecodeError:
                continue

    # Strategy 3: Legacy regex patterns as final fallback
    legacy_patterns = [
        r'(\{"lead_name":.+?"facts"\s*:\s*\[.*?\]\s*\})',
        r'(\{"lead_name":.+?"ready"\s*:\s*(?:true|false)\s*\})',
    ]
    for pattern in legacy_patterns:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                continue

    logger.warning("extract_lead_json: Could not extract lead JSON from LLM response.")
    return None


# ─── Lead form definition: all possible form fields ───
ALL_LEAD_FORM_FIELDS = [
    LeadFormField(field="lead_name", input_type="text", label="Full Name", placeholder="Your Full Name", required=True),
    LeadFormField(field="company_name", input_type="text", label="Organization", placeholder="Company Name", required=True),
    LeadFormField(field="email", input_type="email", label="Email Address", placeholder="Enter your email", required=True),
    LeadFormField(field="phone", input_type="tel", label="Phone Number", placeholder="Enter your phone number", required=False),
    LeadFormField(field="country", input_type="text", label="Country", placeholder="Enter your country", required=True),
    LeadFormField(field="appointment_date", input_type="date", label="Appointment Date", placeholder="DD-MM-YYYY", required=False),
]


def build_lead_form(lead_state_dict: Dict[str, Any]) -> Optional[LeadFormSchema]:
    """Build a lead form containing only the fields that have NOT been collected yet.
    
    Returns None if all fields are already filled.
    """
    missing_fields = []
    for field_def in ALL_LEAD_FORM_FIELDS:
        current_val = lead_state_dict.get(field_def.field, "")
        if not current_val or not str(current_val).strip():
            missing_fields.append(field_def)
    
    if not missing_fields:
        return None
    
    return LeadFormSchema(fields=missing_fields)


def validate_lead_form_request(reply: str) -> bool:
    """Validate that the assistant's reply is explicitly asking the user to fill in their details.
    
    Checks for phrases that indicate the bot is requesting the user to fill a contact form.
    """
    if not reply:
        return False
    reply_lower = reply.lower()
    
    patterns = [
        r"\bfill\s+in\s+(?:your\s+)?details\b",
        r"\byour\s+details\s+(?:below|here)\b",
        r"\bshare\s+(?:your\s+)?(?:contact\s+)?details\b",
        r"\bprovide\s+(?:your\s+)?(?:contact\s+)?details\b",
        r"\bcontact\s+(?:details|information|info)\b",
        r"\bform\s+below\b",
        r"\bfill\s+(?:out|in)\s+(?:the\s+)?form\b",
        r"\byour\s+(?:information|info)\s+(?:below|here)\b",
        r"\bfill\s+(?:these|the)\s+details\b",
        r"\bshare\s+(?:the\s+)?following\s+(?:details|information)\b",
        r"\bpersonalize\b.*\bdetails\b",
        r"\blearn\s+more\s+about\s+you\b",
        r"\bget\s+(?:back|in\s+touch)\b.*\bdetails\b",
        r"\b(?:could|can|would)\s+you\s+(?:please\s+)?(?:share|provide|fill)\b.*\b(?:details|information)\b",
    ]
    return any(re.search(p, reply_lower) for p in patterns)


async def fallback_extract_lead_from_conversation(
    recent_messages: List[Any],
    current_user_message: str,
    current_assistant_reply: str
) -> Optional[dict]:
    """
    Fallback: When the LLM doesn't include the JSON status block in its reply,
    call DeepSeek with a focused extraction prompt to pull out any lead info
    from the full conversation context.
    """
    # Build conversation text from recent messages + current exchange
    conversation_lines = []
    for m in recent_messages:
        role_label = "USER" if m.role == "user" else "ASSISTANT"
        conversation_lines.append(f"{role_label}: {m.content}")
    conversation_lines.append(f"USER: {current_user_message}")
    conversation_lines.append(f"ASSISTANT: {current_assistant_reply}")
    conversation_text = "\n".join(conversation_lines)

    extraction_prompt = textwrap.dedent(f"""\
    Analyze the following conversation and extract any lead/contact information mentioned by the USER,
    as well as determining whether the assistant is asking the user to fill in their contact details.
    Look for:
    - The user's name (e.g., "I'm Suma", "My name is John", or when the assistant addresses them by name)
    - Their company/organization (e.g., "I work at Pfizer", "We are from Google", "our company XYZ")
    - Email address
    - Appointment date (in DD-MM-YYYY format)
    - Phone number
    - Country
    - What they are looking for (notes/requirements)
    - Whether the assistant is asking the user to fill a contact form (expected_input)

    Conversation:
    {conversation_text}

    Respond with ONLY a JSON object in this exact format, nothing else:
    {{"lead_name":"...","company_name":"...","email":"...","appointment_date":"...","phone":"...","country":"...","notes":"...","ready":true/false,"facts":[],"expected_input":"lead_form" | null}}

    Rules:
    - Use "" for any field not mentioned in the conversation.
    - Set "ready" to true if BOTH lead_name and company_name are non-empty.
    - Only extract information that the USER explicitly stated. Do NOT guess or hallucinate.
    - Set "expected_input" to "lead_form" if the assistant is asking the user to fill in their contact details/form in the latest ASSISTANT reply. Otherwise set it to null.
    """)

    try:
        from app.services.llm import call_deepseek
        raw = await call_deepseek([
            {"role": "system", "content": "You are a precise data extraction assistant. Output only valid JSON."},
            {"role": "user", "content": extraction_prompt}
        ])
        logger.debug(f"Fallback extraction raw response: {raw[:500]}")
        # Try to parse the JSON from the response
        extracted = extract_lead_json(raw)
        if extracted:
            # Only return if we actually found something useful (either contact details or expected input field name)
            has_expected_input = bool(extracted.get("expected_input") and str(extracted.get("expected_input")).strip().lower() != "null")
            has_any_info = any(extracted.get(k) for k in ["lead_name", "company_name", "email", "phone"]) or has_expected_input
            if has_any_info:
                logger.debug(f"Fallback extraction result: {json.dumps(extracted)}")
                return extracted
            else:
                logger.debug("Fallback extraction returned no useful fields")
        else:
            # Try direct JSON parse of the raw response
            try:
                raw_clean = raw.strip()
                if raw_clean.startswith("```"):
                    raw_clean = re.sub(r'```(?:json)?\s*', '', raw_clean)
                    raw_clean = raw_clean.rstrip('`').strip()
                obj = json.loads(raw_clean)
                if isinstance(obj, dict):
                    has_expected_input_obj = bool(obj.get("expected_input") and str(obj.get("expected_input")).strip().lower() != "null")
                    if any(obj.get(k) for k in ["lead_name", "company_name", "email", "phone"]) or has_expected_input_obj:
                        logger.debug(f"Fallback direct JSON parse succeeded: {json.dumps(obj)}")
                        return obj
            except (json.JSONDecodeError, Exception):
                pass
            logger.warning("Fallback lead extraction failed to parse JSON")
    except Exception as e:
        logger.error(f"Fallback extraction error: {e}", exc_info=True)

    return None


def strip_lead_json(text: str) -> str:
    """Remove the lead JSON block from the response shown to the user."""
    # 1. Strip any code block containing lead_name
    text = re.sub(r'```json\s*\n?\s*\{.*?"lead_name".*?\}\s*\n?\s*```', '', text, flags=re.DOTALL)
    
    # 2. Strip any raw JSON block starting with {"lead_name" using brace counting
    for match in list(re.finditer(r'\{\s*"lead_name"', text)):
        json_str = _extract_json_by_braces(text, match.start())
        if json_str:
            text = text.replace(json_str, "")
            
    # 3. Fallback cleanups of legacy regexes just in case
    text = re.sub(r'\{"lead_name":.+?"ready"\s*:\s*(?:true|false)\s*\}', '', text, flags=re.DOTALL)
    text = re.sub(r'\{"lead_name":.+?"facts"\s*:\s*\[.*?\]\s*\}', '', text, flags=re.DOTALL)
    return text.strip()


async def background_compression_task(conversation_id: str):
    """Run conversation compression in background with a fresh DB connection."""
    async with AsyncSessionLocal() as db:
        try:
            await MemoryService.compress_conversation(db, conversation_id)
        except Exception as e:
            logger.error(f"Error running background conversation compression: {e}")



async def process_post_chat(
    db: AsyncSession,
    conversation_id: str,
    user_id: str,
    user_message: str,
    assistant_reply: str,
    lead_json: Optional[Dict[str, Any]],
    background_tasks: BackgroundTasks
) -> tuple[Dict[str, Any], bool, str]:
    """Handles post-response DB updates, episodic memory storage, ERPNext syncing,
    and user identity resolution.
    
    Returns:
        Tuple of (lead_data dict, lead_saved bool, resolved_user_id str)
        The resolved_user_id may differ from the input user_id if the user was
        identified as an existing known user and sessions were merged.
    """
    newly_filled = {}
    resolved_user_id = user_id

    # 1. Update Lead State from LLM-extracted JSON (if any)
    if lead_json:
        logger.debug(f"Lead JSON extracted: {json.dumps(lead_json)}")
        # Merge new lead fields into the DB state (accumulates across turns)
        _, newly_filled = await LeadService.update_lead_state(db, conversation_id, user_id, lead_json)
        if newly_filled:
            logger.info(f"Lead fields collected: {list(newly_filled.keys())}")

        # 1b. IDENTITY RESOLUTION: If email or phone was newly collected,
        #     check if this anonymous user is actually a known user
        collected_email = newly_filled.get("email") or (lead_json.get("email") if lead_json else None)
        collected_phone = newly_filled.get("phone") or (lead_json.get("phone") if lead_json else None)

        if collected_email or collected_phone:
            try:
                resolved_user_id, was_merged = await UserIdentityService.resolve_user_identity(
                    db=db,
                    current_user_id=user_id,
                    email=collected_email,
                    phone=collected_phone
                )
                if was_merged:
                    logger.info(
                        f"User identity resolved: {user_id} -> {resolved_user_id} "
                        f"(sessions merged)"
                    )
                    # Use the resolved user_id for all subsequent operations
                    user_id = resolved_user_id
            except Exception as e:
                logger.error(f"Error resolving user identity: {e}", exc_info=True)
                await db.rollback()
                # Non-fatal: continue with original user_id

        # Store Episodic Memories (using resolved user_id)
        if "facts" in lead_json and isinstance(lead_json["facts"], list):
            for fact in lead_json["facts"]:
                if fact and isinstance(fact, str):
                    await MemoryService.add_episodic_memory(db, user_id, fact)
    else:
        logger.debug(f"No lead JSON in LLM response for session {conversation_id}")

    # 2. ALWAYS attempt ERP sync using the accumulated DB state
    #    This ensures lead creation even if name came in turn 1 and company in turn 2
    lead_state = await LeadService.get_or_create_lead_state(db, conversation_id, user_id)
    has_name = bool(lead_state.lead_name)
    has_company = bool(lead_state.company_name)
    logger.debug(
        f"Pre-sync state: name='{lead_state.lead_name}', "
        f"company='{lead_state.company_name}', saved={lead_state.lead_saved}, "
        f"lead_id='{lead_state.lead_id}'"
    )

    try:
        sync_result = await LeadService.sync_lead_to_erpnext(db, lead_state, bool(newly_filled))
        logger.debug(f"Lead sync result: {sync_result}")
    except Exception as e:
        logger.error(f"Error syncing lead to ERPNext: {e}", exc_info=True)
        await db.rollback()

    # 3. Save User and Assistant Messages & generate vector embeddings
    await MemoryService.store_message_and_embed(db, conversation_id, user_id, "user", user_message)
    await MemoryService.store_message_and_embed(db, conversation_id, user_id, "assistant", assistant_reply)

    # 4. Schedule Background Compression Check (every 25 messages)
    background_tasks.add_task(background_compression_task, conversation_id)

    # Re-fetch lead state to get the real lead_saved status (updated by sync_lead_to_erpnext)
    await db.refresh(lead_state)
    logger.debug(f"Final lead_saved={lead_state.lead_saved}")
    
    lead_data = {
        "lead_name": lead_state.lead_name,
        "company_name": lead_state.company_name,
        "email": lead_state.email,
        "appointment_date": lead_state.appointment_date,
        "phone": lead_state.phone,
        "country": lead_state.country,
        "notes": lead_state.notes,
    }

    return lead_data, lead_state.lead_saved, resolved_user_id

async def build_dynamic_prompt(
    db: AsyncSession,
    session_id: str,
    user_id: str,
    user_message: str
) -> tuple[str, List[Dict[str, Any]], List[Dict[str, Any]], List[Message]]:
    """Builds the fully context-enriched system prompt including RAG, user memories, and case studies.
    
    Returns:
        Tuple of (system_prompt, knowledge_results, case_study_results, recent_messages)
    """
    # 1. Get/Create conversation metadata
    conv = await MemoryService.create_conversation_if_not_exists(db, session_id, user_id)

    # 2. Get Recent Chat Context (last 10 messages)
    recent_messages = await MemoryService.get_recent_messages(db, session_id, limit=10)
    recent_msg_ids = {m.id for m in recent_messages}

    # 3. Retrieve User Episodic Memory summaries
    episodic_results = await MemoryService.search_episodic_memories(db, user_id, user_message)
    episodic_text = "\n".join(
        [f"- {e['fact']}" for e in episodic_results]
    ) if episodic_results else "No historical user profile facts recorded yet."

    # 4. Retrieve Vector Conversation Memory (across all user sessions, excluding recent window)
    memory_results = await MemoryService.search_memory(
        db,
        user_id=user_id,
        query=user_message,
        recent_message_ids=recent_msg_ids,
        candidate_k=settings.TOP_K_MEMORY,
        final_k=settings.TOP_K_RERANKED,
        threshold=settings.SIMILARITY_THRESHOLD_MEMORY
    )
    memory_text = "\n".join(
        [f"- [{m['role'].upper()}]: {m['content']}" for m in memory_results]
    ) if memory_results else "No relevant past conversation memories found."

    # 5. Retrieve Website Knowledge using vector search + cross-encoder rerank
    knowledge_results = await KnowledgeService.search_knowledge(
        db,
        query=user_message,
        candidate_k=settings.TOP_K_KNOWLEDGE,
        final_k=settings.TOP_K_RERANKED,
        threshold=settings.SIMILARITY_THRESHOLD_KNOWLEDGE
    )
    knowledge_text = "\n\n---\n\n".join(
        [f"[Source: {k['title']}]\n{k['text']}\nLink: {k['url']}" for k in knowledge_results]
    ) if knowledge_results else "No specific website knowledge found."

    # 6. Retrieve Relevant Case Studies (dedicated search over case_study-tagged chunks with lowered threshold)
    case_study_results = await KnowledgeService.search_case_studies(
        db,
        query=user_message,
        candidate_k=10,
        final_k=2,
        threshold=0.30,
    )
    case_study_text = "\n\n---\n\n".join(
        [f"[Case Study: {cs['title']}]\n{cs['text']}\nRead more: {cs['url']}" for cs in case_study_results]
    ) if case_study_results else "No relevant case studies found."

    # 7. Count User Messages to determine conversation phase
    stmt = select(Message).where(Message.conversation_id == session_id, Message.role == "user")
    user_msg_result = await db.execute(stmt)
    user_msg_count = len(user_msg_result.scalars().all()) + 1

    # 8. Formulate current lead status
    lead_state = await LeadService.get_or_create_lead_state(db, session_id, user_id)
    lead_status_json = {
        "lead_name": lead_state.lead_name,
        "company_name": lead_state.company_name,
        "email": lead_state.email,
        "appointment_date": lead_state.appointment_date,
        "phone": lead_state.phone,
        "country": lead_state.country,
        "notes": lead_state.notes,
    }

    # 9. Determine phase based on state in DB and conversation history
    has_recommendation = False
    for msg in recent_messages:
        if msg.role == "assistant" and any(keyword in msg.content.lower() for keyword in ["recommend", "biztechnosys", "sitecore"]):
            has_recommendation = True

    if lead_state.appointment_date:
        phase_instruction = (
            f"\n\n## CURRENT PHASE: POST-LEAD & BOOKED (Turn {user_msg_count})\n"
            f"The user has submitted contact details AND booked an appointment for {lead_state.appointment_date}. "
            "Follow the Post-Lead Rules strictly:\n"
            "- If the user asks follow-up questions about the SAME requirement/service, "
            "say our experts will discuss everything in detail during the scheduled call.\n"
            "- If the user mentions a NEW/DIFFERENT requirement, recommend the best-fit service, "
            "include a case study if available, and UPDATE the 'notes' field in your JSON block.\n"
            "- If the user says no or is wrapping up, say goodbye warmly."
        )
    elif lead_state.lead_saved:
        phase_instruction = (
            f"\n\n## CURRENT PHASE: DISCOVERY CALL INVITATION & BOOKING (Turn {user_msg_count})\n"
            "The lead is successfully created in ERP/CRM. You must now ask the user:\n"
            "\"Would you like to schedule a free discovery call with our team?\"\n"
            "Rules for this phase:\n"
            "1. If the customer declines/says no: End the conversation gracefully and set expected_input to null.\n"
            "2. If the customer agrees/says yes:\n"
            f"   - Check if we have their email address. Current email in DB is: '{lead_state.email}'.\n"
            "   - If their email is empty/missing, you MUST ask them to provide their email address first before booking. Do NOT set expected_input to 'booking_form' yet.\n"
            "   - If/once we have their email, prompt them to book using the inline form and set expected_input to 'booking_form'.\n"
        )
    elif has_recommendation:
        phase_instruction = (
            f"\n\n## CURRENT PHASE: LEAD CAPTURE (Turn {user_msg_count})\n"
            "You have recommended the best-fit service/product.\n"
            "Ask the user to fill in their details in the form below so the team can prepare a proposal.\n"
            "Set expected_input to \"lead_form\" in the JSON block."
        )
    elif user_msg_count <= 1:
        phase_instruction = (
            f"\n\n## CURRENT PHASE: PROJECT DISCOVERY (Turn {user_msg_count})\n"
            "This is the first response. Use ONE warm greeting line, then IMMEDIATELY ask "
            "what project or business challenge brought them here. "
            "Do NOT ask for contact details or recommend products yet."
        )
    else:
        phase_instruction = (
            f"\n\n## CURRENT PHASE: PRE-SALES QUALIFYING & ANALYSIS (Turn {user_msg_count})\n"
            "Analyze the user's requirements. If additional information is needed, ask intelligent "
            "pre-sales qualification questions one by one or in small sets to better understand: "
            "business domain, project type, challenges, budget, timeline, technical requirements.\n"
            "Continue asking questions until you have enough context to make an accurate recommendation.\n"
            "Once you have sufficient context to make a recommendation, transition immediately to the recommendation step:\n"
            "1. Recommends the most suitable Biztechnosys service(s) or product(s).\n"
            "2. Explains why it fits.\n"
            "3. Shares relevant case studies if available in context.\n"
            "4. Asks the user to fill in their details in the lead form, and sets expected_input to \"lead_form\"."
        )

    # 10. Assemble components
    prompt_builder = [SYSTEM_PROMPT]
    
    if conv.summary:
        prompt_builder.append(f"\n\n## Summary of Previous Messages\n{conv.summary}")
        
    prompt_builder.append(f"\n\n## User Profile (Episodic Memory)\n{episodic_text}")
    prompt_builder.append(f"\n\n## Retrieved Conversation Memory (Relevant Past Context)\n{memory_text}")
    prompt_builder.append(f"\n\n## Retrieved Website Knowledge\n{knowledge_text}")
    prompt_builder.append(f"\n\n## Relevant Case Studies\n{case_study_text}")
    prompt_builder.append(f"\n\n## Current Lead Status (already collected)\n{json.dumps(lead_status_json)}")
    prompt_builder.append(phase_instruction)

    return "".join(prompt_builder), knowledge_results, case_study_results, recent_messages


@router.post("/chat", response_model=ChatResponse)
async def chat(
    req: ChatRequest, 
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """
    Standard Chat Endpoint (Non-Streaming).
    
    Retrieves knowledge, long-term memory, user facts, conversation summaries,
    constructs the dynamic prompt, issues DeepSeek request, updates lead records,
    and returns a full JSON response.
    """
    session_id = req.session_id or str(uuid.uuid4())
    user_id = req.user_id or str(uuid.uuid4())

    # Check if this is an inline booking submission
    booking_match = re.match(r"^Submit Booking:\s*Date:\s*([\d-]+),\s*Time:\s*(.+)$", req.message.strip(), re.IGNORECASE)
    if booking_match:
        booking_date = booking_match.group(1).strip()
        booking_time = booking_match.group(2).strip()
        
        # 1. Fetch Lead State
        stmt = select(LeadState).where(LeadState.conversation_id == session_id)
        res = await db.execute(stmt)
        lead_state = res.scalar_one_or_none()
        
        if not lead_state:
            lead_state = LeadState(
                conversation_id=session_id,
                lead_name="Valued Client",
                email="client@example.com",
                company_name="",
                notes=""
            )
            db.add(lead_state)
            await db.commit()
            
        lead_name = lead_state.lead_name or "Valued Client"
        user_email = lead_state.email or "client@example.com"
        company_name = lead_state.company_name or ""
        notes = lead_state.notes or ""
        
        formatted_appointment = f"{booking_date} {booking_time}"
        lead_state.appointment_date = formatted_appointment
        
        # Schedule Outlook Event
        from app.services.outlook import OutlookService
        calendar_success = await OutlookService.create_calendar_event(
            lead_name=lead_name,
            user_email=user_email,
            company_name=company_name,
            date_str=booking_date,
            time_str=booking_time,
            notes=notes
        )
        
        # Send Confirmation Email
        email_success = await OutlookService.send_confirmation_emails(
            lead_name=lead_name,
            user_email=user_email,
            company_name=company_name,
            date_str=booking_date,
            time_str=booking_time,
            notes=notes
        )
        
        # Save to DB and sync to ERPNext
        await db.commit()
        try:
            await LeadService.sync_lead_to_erpnext(db, lead_state, newly_filled=True)
        except Exception as e:
            logger.error(f"Error syncing appointment date to ERPNext: {e}")
            
        confirmation_msg = (
            f"🎉 **Discovery Call Confirmed!**\n\n"
            f"Your appointment with the Biztechnosys team is scheduled for **{booking_date} at {booking_time}** (IST).\n\n"
            f"A calendar invitation and confirmation email have been sent to **{user_email}** and **chethana@biztechnosys.com**.\n\n"
            f"**Do you have any other project or requirement we can help you with?**"
        )
        
        # Add messages to chat memory
        await MemoryService.store_message_and_embed(db, session_id, user_id, "user", req.message)
        await MemoryService.store_message_and_embed(db, session_id, user_id, "assistant", confirmation_msg)
        
        lead_data = {
            "lead_name": lead_state.lead_name,
            "company_name": lead_state.company_name,
            "email": lead_state.email,
            "appointment_date": lead_state.appointment_date,
            "phone": lead_state.phone,
            "country": lead_state.country,
            "notes": lead_state.notes,
        }
        
        return ChatResponse(
            reply=confirmation_msg,
            session_id=session_id,
            user_id=user_id,
            sources=[],
            case_studies=[],
            lead_collected=LeadStateSchema(**lead_data),
            lead_saved=lead_state.lead_saved,
            lead_form=None,
            booking_form=None,
            conversation_complete=True
        )

    # Compile prompt and context
    system_prompt, knowledge_results, case_study_results, recent_messages = await build_dynamic_prompt(
        db, session_id, user_id, req.message
    )

    llm_messages = [{"role": "system", "content": system_prompt}]
    for m in recent_messages:
        llm_messages.append({"role": m.role, "content": m.content})
    llm_messages.append({"role": "user", "content": req.message})

    # Call LLM
    raw_reply = await call_deepseek(llm_messages)

    # Process extraction, state updates, ERPNext sync, logging, compression
    lead_json = extract_lead_json(raw_reply)
    clean_reply = strip_lead_json(raw_reply)

    # FALLBACK: If LLM didn't output JSON block, extract from conversation
    if not lead_json:
        logger.debug(f"Primary JSON extraction failed for session {session_id}, running fallback")
        lead_json = await fallback_extract_lead_from_conversation(
            recent_messages, req.message, clean_reply
        )
        if lead_json:
            logger.debug(f"Fallback extraction succeeded: {json.dumps(lead_json)}")
        else:
            logger.warning(f"Lead JSON extraction failed for session {session_id}")

    lead_data, lead_just_saved, resolved_user_id = await process_post_chat(
        db=db,
        conversation_id=session_id,
        user_id=user_id,
        user_message=req.message,
        assistant_reply=clean_reply,
        lead_json=lead_json,
        background_tasks=background_tasks
    )

    # Fetch lead_state to fix NameError below
    lead_state = await LeadService.get_or_create_lead_state(db, session_id, resolved_user_id)

    # Build sources info list
    sources = []
    seen_urls = set()
    for r in knowledge_results:
        if r["url"] not in seen_urls:
            sources.append(SourceInfo(title=r["title"], url=r["url"], score=round(r["score"], 3)))
            seen_urls.add(r["url"])

    # Build lead form & booking form
    lead_form_data = None
    booking_form_data = None

    if lead_json and lead_json.get("expected_input"):
        expected = str(lead_json["expected_input"]).strip().lower()
        if expected == "lead_form" and not lead_state.lead_saved:
            lead_form_data = build_lead_form(lead_data)
        elif expected == "booking_form" and lead_state.lead_saved and not lead_state.appointment_date:
            booking_form_data = {
                "button_text": "Book Discovery Call",
                "title": "Schedule Discovery Call"
            }

    # Build case studies info list
    case_studies = []
    seen_cs_urls = set()
    for cs in case_study_results:
        if cs["url"] not in seen_cs_urls:
            # Use first ~200 chars of text as summary
            summary_text = cs["text"][:200].rsplit(" ", 1)[0] + "..." if len(cs["text"]) > 200 else cs["text"]
            case_studies.append(CaseStudyInfo(
                title=cs["title"],
                url=cs["url"],
                summary=summary_text,
                score=round(cs["score"], 3)
            ))
            seen_cs_urls.add(cs["url"])

    return ChatResponse(
        reply=clean_reply,
        session_id=session_id,
        user_id=resolved_user_id,
        sources=sources[:3],
        case_studies=case_studies,
        lead_collected=LeadStateSchema(**lead_data),
        lead_saved=lead_just_saved,
        lead_form=lead_form_data,
        booking_form=booking_form_data,
        conversation_complete=lead_just_saved and bool(lead_state.appointment_date)
    )



@router.post("/chat/stream")
async def chat_stream(
    req: ChatRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """
    Server-Sent Events (SSE) Chat Streaming Endpoint.
    
    Streams chunks of reply text in real-time. Yields a final metadata payload 
    (sources, collected lead state, sync flag) after completion.
    """
    session_id = req.session_id or str(uuid.uuid4())
    user_id = req.user_id or str(uuid.uuid4())

    # Check if this is an inline booking submission
    booking_match = re.match(r"^Submit Booking:\s*Date:\s*([\d-]+),\s*Time:\s*(.+)$", req.message.strip(), re.IGNORECASE)
    if booking_match:
        booking_date = booking_match.group(1).strip()
        booking_time = booking_match.group(2).strip()
        
        async def event_generator_booking():
            # 1. Fetch Lead State
            stmt = select(LeadState).where(LeadState.conversation_id == session_id)
            res = await db.execute(stmt)
            lead_state = res.scalar_one_or_none()
            
            if not lead_state:
                lead_state = LeadState(
                    conversation_id=session_id,
                    lead_name="Valued Client",
                    email="client@example.com",
                    company_name="",
                    notes=""
                )
                db.add(lead_state)
                await db.commit()
                
            lead_name = lead_state.lead_name or "Valued Client"
            user_email = lead_state.email or "client@example.com"
            company_name = lead_state.company_name or ""
            notes = lead_state.notes or ""
            
            formatted_appointment = f"{booking_date} {booking_time}"
            lead_state.appointment_date = formatted_appointment
            
            # Schedule Outlook Event
            from app.services.outlook import OutlookService
            calendar_success = await OutlookService.create_calendar_event(
                lead_name=lead_name,
                user_email=user_email,
                company_name=company_name,
                date_str=booking_date,
                time_str=booking_time,
                notes=notes
            )
            
            # Send Confirmation Email
            email_success = await OutlookService.send_confirmation_emails(
                lead_name=lead_name,
                user_email=user_email,
                company_name=company_name,
                date_str=booking_date,
                time_str=booking_time,
                notes=notes
            )
            
            # Save to DB and sync to ERPNext
            await db.commit()
            try:
                await LeadService.sync_lead_to_erpnext(db, lead_state, newly_filled=True)
            except Exception as e:
                logger.error(f"Error syncing appointment date to ERPNext: {e}")
                
            confirmation_msg = (
                f"🎉 **Discovery Call Confirmed!**\n\n"
                f"Your appointment with the Biztechnosys team is scheduled for **{booking_date} at {booking_time}** (IST).\n\n"
                f"A calendar invitation and confirmation email have been sent to **{user_email}** and **chethana@biztechnosys.com**.\n\n"
                f"**Do you have any other project or requirement we can help you with?**"
            )
            
            # Add messages to chat memory
            await MemoryService.store_message_and_embed(db, session_id, user_id, "user", req.message)
            await MemoryService.store_message_and_embed(db, session_id, user_id, "assistant", confirmation_msg)
            
            lead_data = {
                "lead_name": lead_state.lead_name,
                "company_name": lead_state.company_name,
                "email": lead_state.email,
                "appointment_date": lead_state.appointment_date,
                "phone": lead_state.phone,
                "country": lead_state.country,
                "notes": lead_state.notes,
            }
            
            # Stream response in chunks to simulate typing
            chunk_size = 20
            for i in range(0, len(confirmation_msg), chunk_size):
                chunk = confirmation_msg[i:i+chunk_size]
                yield f"data: {json.dumps({'type': 'content', 'content': chunk})}\n\n"
                await asyncio.sleep(0.05)
                
            metadata = {
                "type": "metadata",
                "session_id": session_id,
                "user_id": user_id,
                "sources": [],
                "case_studies": [],
                "lead": lead_data,
                "lead_saved": lead_state.lead_saved,
                "lead_form": None,
                "booking_form": None,
                "conversation_complete": True
            }
            yield f"data: {json.dumps(metadata)}\n\n"
            
        return StreamingResponse(event_generator_booking(), media_type="text/event-stream")

    # Compile prompt and context
    system_prompt, knowledge_results, case_study_results, recent_messages = await build_dynamic_prompt(
        db, session_id, user_id, req.message
    )

    llm_messages = [{"role": "system", "content": system_prompt}]
    for m in recent_messages:
        llm_messages.append({"role": m.role, "content": m.content})
    llm_messages.append({"role": "user", "content": req.message})

    async def event_generator():
        full_reply = ""
        yielded_len = 0
        stop_streaming = False
        
        # Markers indicating the start of the lead extraction JSON block
        MARKERS = ["```json", '{"lead_name"']
        
        # Generate all prefixes for holding back partial matches
        PREFIXES = []
        for m in MARKERS:
            for i in range(1, len(m)):
                PREFIXES.append(m[:i])
        PREFIXES.sort(key=len, reverse=True)
        
        async with httpx.AsyncClient(timeout=60) as client:
            try:
                async with client.stream(
                    "POST",
                    f"{settings.DEEPSEEK_BASE_URL}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "deepseek-chat",
                        "messages": llm_messages,
                        "temperature": 0.7,
                        "max_tokens": 1024,
                        "stream": True,
                    }
                ) as response:
                    response.raise_for_status()
                    
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            data_str = line[6:].strip()
                            if data_str == "[DONE]":
                                break
                            try:
                                chunk_json = json.loads(data_str)
                                content = chunk_json["choices"][0]["delta"].get("content", "")
                                if content:
                                    full_reply += content
                                    
                                    if not stop_streaming:
                                        # Stream-buffering logic to suppress JSON status block
                                        marker_pos = -1
                                        for m in MARKERS:
                                            pos = full_reply.find(m)
                                            if pos != -1:
                                                if marker_pos == -1 or pos < marker_pos:
                                                    marker_pos = pos
                                                    
                                        if marker_pos != -1:
                                            # We hit the marker! Yield everything up to marker_pos, and stop streaming further text.
                                            text_to_yield = full_reply[yielded_len:marker_pos]
                                            yielded_len = marker_pos
                                            stop_streaming = True
                                            if text_to_yield:
                                                yield f"data: {json.dumps({'type': 'content', 'content': text_to_yield})}\n\n"
                                        else:
                                            # Check if full_reply ends with a prefix of any marker to hold back partial matches
                                            matched_prefix_len = 0
                                            for prefix in PREFIXES:
                                                if full_reply.endswith(prefix):
                                                    matched_prefix_len = len(prefix)
                                                    break
                                                    
                                            end_pos = len(full_reply) - matched_prefix_len
                                            text_to_yield = full_reply[yielded_len:end_pos]
                                            yielded_len = end_pos
                                            if text_to_yield:
                                                yield f"data: {json.dumps({'type': 'content', 'content': text_to_yield})}\n\n"
                            except Exception:
                                continue
            except Exception as e:
                logger.error(f"Error streaming from DeepSeek: {e}")
                yield f"data: {json.dumps({'type': 'error', 'detail': 'Streaming connection lost.'})}\n\n"
                return

        # Post-processing after streaming is finished
        lead_json = extract_lead_json(full_reply)
        clean_reply = strip_lead_json(full_reply)

        # FALLBACK: If LLM didn't output JSON block, extract from conversation
        if not lead_json:
            logger.debug(f"Primary JSON extraction failed for stream session {session_id}, running fallback")
            lead_json = await fallback_extract_lead_from_conversation(
                recent_messages, req.message, clean_reply
            )
            if lead_json:
                logger.debug(f"Fallback extraction succeeded for stream: {json.dumps(lead_json)}")
            else:
                logger.warning(f"Lead JSON extraction failed for stream session {session_id}")

        # Update DB state, sync leads, save message logs, run compression
        lead_data, lead_just_saved, resolved_user_id = await process_post_chat(
            db=db,
            conversation_id=session_id,
            user_id=user_id,
            user_message=req.message,
            assistant_reply=clean_reply,
            lead_json=lead_json,
            background_tasks=background_tasks
        )

        # Fetch lead_state to fix NameError below
        lead_state = await LeadService.get_or_create_lead_state(db, session_id, resolved_user_id)

        # Format sources list
        sources = []
        seen_urls = set()
        for r in knowledge_results:
            if r["url"] not in seen_urls:
                sources.append({"title": r["title"], "url": r["url"], "score": round(r["score"], 3)})
                seen_urls.add(r["url"])

        # Build lead form & booking form
        lead_form_data = None
        booking_form_data = None

        if lead_json and lead_json.get("expected_input"):
            expected = str(lead_json["expected_input"]).strip().lower()
            if expected == "lead_form" and not lead_state.lead_saved:
                lead_form_data = build_lead_form(lead_data)
            elif expected == "booking_form" and lead_state.lead_saved and not lead_state.appointment_date:
                booking_form_data = {
                    "button_text": "Book Discovery Call",
                    "title": "Schedule Discovery Call"
                }

        # Build case studies info list
        case_studies_list = []
        seen_cs_urls = set()
        for cs in case_study_results:
            if cs["url"] not in seen_cs_urls:
                summary_text = cs["text"][:200].rsplit(" ", 1)[0] + "..." if len(cs["text"]) > 200 else cs["text"]
                case_studies_list.append({
                    "title": cs["title"],
                    "url": cs["url"],
                    "summary": summary_text,
                    "score": round(cs["score"], 3)
                })
                seen_cs_urls.add(cs["url"])

        # Send final metadata event (use resolved_user_id so frontend updates its stored identity)
        lead_form_serialized = lead_form_data.model_dump() if lead_form_data else None
        metadata = {
            "type": "metadata",
            "session_id": session_id,
            "user_id": resolved_user_id,
            "sources": sources[:3],
            "case_studies": case_studies_list,
            "lead": lead_data,
            "lead_saved": lead_just_saved,
            "lead_form": lead_form_serialized,
            "booking_form": booking_form_data,
            "conversation_complete": lead_just_saved and bool(lead_state.appointment_date)
        }
        yield f"data: {json.dumps(metadata)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
