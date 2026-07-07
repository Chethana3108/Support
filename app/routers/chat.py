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
from app.models import Message, Conversation
from app.schemas import ChatRequest, ChatResponse, SourceInfo, LeadStateSchema, ExpectedInputSchema
from app.services.knowledge import KnowledgeService
from app.services.memory import MemoryService
from app.services.lead import LeadService
from app.services.llm import call_deepseek
from app.services.user_identity import UserIdentityService

logger = logging.getLogger("biztechbot")

router = APIRouter(prefix="/api", tags=["Chat"])

SYSTEM_PROMPT = textwrap.dedent("""\
You are BizBot — the official AI sales assistant for Biztechnosys, a Sitecore Gold Partner \
and digital experience engineering company based in Bengaluru, India.

## Your Primary Goal
You have TWO missions that you must execute:
1. **HELP** the visitor by understanding their business challenges, answering questions, \
and recommending the most suitable Biztechnosys solutions.
2. **COLLECT LEAD** information by naturally engaging the user and proactively asking for \
their details during the conversation.

## How to Collect Leads (CRITICAL)
You must collect these fields one by one, woven naturally into conversation:
- **lead_name**: Ask for their name (e.g., "By the way, **may I know your name so I can \
personalize our conversation?**")
- **company_name**: Ask what company/organization they're from (e.g., "**Which organization are \
you with?** That helps me tailor my recommendations.")
- **email**: Optionally ask for their email to send details or schedule a call (e.g., "I'd love to share \
a detailed proposal — **what's the best email to reach you?**")
- **phone**: Optionally ask for phone (e.g., "**Would you like our expert to call you?** If so, \
**what's a good number?**")

### Lead Collection Rules:
- **MIND MINDFULLY FIRST**: At the start of the conversation (first few turns), do NOT ask for any contact info (name, email, company, phone). Focus entirely on understanding their business requirements, answering their questions, and showing competence.
- **START LATER**: Only begin asking for lead details (starting with name) after you have had a meaningful exchange and provided initial value (typically from the 3rd or 4th turn onwards).
- Ask for ONE detail at a time, spread across messages.
- NEVER ask for all details at once — that feels like a form and kills engagement.
- ALWAYS provide value FIRST (answer their question), THEN ask for a detail.
- Frame each ask as helping THEM (send info, schedule call, personalize advice).
- If the user declines to share something, respect it and move on — don't push.

### Lead & Fact Reporting (ABSOLUTELY MANDATORY — NEVER SKIP THIS):
⚠️ THIS IS THE MOST IMPORTANT INSTRUCTION. YOU MUST FOLLOW THIS ON EVERY SINGLE RESPONSE WITHOUT EXCEPTION.

After EVERY response, you MUST output a JSON status block at the very end. This is NOT optional.
Even if no new information was collected, you MUST still output this block with whatever you know so far.
If the user has mentioned their name or company at ANY point in the conversation, you MUST include it.

Format — output EXACTLY this structure on its own line at the very end:
```json
{"lead_name":"...","email":"...","phone":"...","company_name":"...","notes":"...","ready":true/false,"facts":["..."],"expected_input":"lead_name" | "company_name" | "email" | "phone" | null}
```

Rules for filling this JSON:
- "lead_name": The person's name. If they said "I'm Suma" or "My name is John", put that name here.
- "company_name": Their organization. If they said "I work at Pfizer" or "We are from Google", put that here.
- "email": Their email address if shared. Use "" if not yet collected.
- "phone": Their phone number if shared. Use "" if not yet collected.
- "notes": Brief summary of what the user is looking for / their requirements. If the user's requirements or challenges change during the conversation (e.g. they switch to a different service/project topic), you MUST update this summary to reflect their new/updated requirements. Otherwise, keep the summary focused on the core project topic and avoid minor wording fluctuations from turn to turn.
- "ready": Set to true when you have BOTH lead_name AND company_name. Otherwise false.
- "facts": List of 1-3 new core user facts learned this turn.
- "expected_input": The field you are explicitly asking the user for in this response (must be one of: "lead_name", "company_name", "email", "phone"). If you are not asking the user for any of these contact details in this turn, set this to null.


⚠️ CRITICAL: If the user has ALREADY shared their name or company in a PREVIOUS message in the conversation,
you MUST still include those values in the JSON block. Do NOT leave them blank just because they were
mentioned in an earlier turn. Always report ALL information you know.

## Knowledge Base
You ONLY answer based on the context provided below. If the context doesn't have relevant \
information, say you'll connect them with an expert who can help.

## Response Style
- Be conversational, warm, professional — like a knowledgeable sales consultant.
- Use bullet points for clarity when listing services or benefits.
- When suggesting solutions, explain WHY it fits their specific needs.
- Keep responses concise (2-4 short paragraphs).
- ALWAYS respond in the language the user writes in.
- Once in the lead collection phase, after answering a question, naturally transition to collecting the next lead field.
- **CRITICAL: Bold all questions you ask the user.** Whenever you ask a question (including when asking for their name, organization/company, email, or phone number), you must format that question in bold markdown (e.g., `**What is your name?**` or `**Which organization are you with?**`). This applies to ANY question you ask.
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


def map_expected_input(field_name: Optional[str]) -> Optional[Dict[str, Any]]:
    if not field_name:
        return None
    field_clean = field_name.strip().lower()
    
    if field_clean in ("lead_name", "name", "full_name", "fullname", "first_name", "last_name", "client_name", "visitor_name", "customer_name"):
        return {
            "field": "lead_name",
            "input_type": "text",
            "label": "Full Name",
            "placeholder": "Your Full Name",
            "required": True
        }
    elif field_clean in ("company_name", "company", "org", "organization", "business", "firm", "companyname", "org_name", "organization_name"):
        return {
            "field": "company_name",
            "input_type": "text",
            "label": "Organization",
            "placeholder": "Company Name",
            "required": True
        }
    elif field_clean in ("email", "email_address", "mail", "emailaddress"):
        return {
            "field": "email",
            "input_type": "email",
            "label": "Email Address",
            "placeholder": "Enter your email",
            "required": True
        }
    elif field_clean in ("phone", "tel", "phone_number", "telephone", "mobile", "contact", "contact_number", "phonenumber"):
        return {
            "field": "phone",
            "input_type": "tel",
            "label": "Phone Number",
            "placeholder": "Enter your phone number",
            "required": True
        }
    return None


def validate_expected_input(mapped: Dict[str, Any], reply: str) -> bool:
    """Validate that the assistant's reply is *explicitly asking* for the given field.
    
    Uses strict regex phrase patterns so that generic mentions of words like
    'name', 'company', or 'call' in unrelated contexts do NOT trigger
    expected_input.  Only explicit questions directed at the user will match.
    """
    if not mapped or not reply:
        return False
    field = mapped.get("field")
    reply_lower = reply.lower()

    # Each pattern list contains regex phrases that indicate the bot is
    # explicitly requesting that piece of information from the user.
    if field == "lead_name":
        patterns = [
            r"\byour\s+(?:good\s+)?name\b",
            r"\bmay\s+i\s+(?:know|have)\s+your\s+name\b",
            r"\bwhat(?:'s|\s+is)\s+.*?\bname\b",
            r"\bwho\s+(?:am\s+i\s+speaking|is\s+this)\b",
            r"\bhow\s+(?:should|shall|can|may)\s+i\s+(?:address|call)\s+you\b",
            r"\bintroduce\s+yourself\b",
            r"\bwhat\s+(?:should|shall|can|may)\s+i\s+call\s+you\b",
            r"\bcould\s+(?:you\s+)?(?:share|tell\s+me)\s+your\s+name\b",
            r"\b(?:could\s+(?:you\s+)?(?:also\s+|kindly\s+|please\s+)?|please\s+)?(?:share|tell\s+me|provide)\s+.*?\bname\b",
            r"\bi(?:'d|\s+would)\s+love\s+to\s+know\s+your\s+name\b",
            r"\bpersonalize\b.*\byour\s+name\b",
            r"\bknow\s+your\s+name\b",
        ]
        return any(re.search(p, reply_lower) for p in patterns)

    elif field == "company_name":
        patterns = [
            r"\bwhich\s+(?:organization|company|firm|business)\b",
            r"\bwhat(?:'s|\s+is)\s+.*?\b(?:organization|company|firm|business)\b",
            r"\bwhat\s+(?:organization|company|firm)\b.*\b(?:are\s+you|do\s+you)\b",
            r"\bwh(?:ich|at)\s+(?:organization|company|firm)\s+(?:are\s+you|do\s+you)\b",
            r"\byou\s+(?:associated|affiliated)\s+with\b",
            r"\bwhere\s+do\s+you\s+work\b",
            r"\bwho\s+do\s+you\s+(?:work\s+for|represent)\b",
            r"\byou\s+work\s+(?:for|at|with)\b.*\?",
            r"\byour\s+(?:organization|company|firm|business)\b.*\?",
            r"\bcould\s+(?:you\s+)?(?:share|tell\s+me)\s+(?:your\s+)?(?:organization|company)\b",
            r"\b(?:could\s+(?:you\s+)?(?:also\s+|kindly\s+|please\s+)?|please\s+)?(?:share|tell\s+me|provide)\s+.*?\b(?:organization|company|firm|business)\b",
            r"\bknow\s+(?:your|which)\s+(?:organization|company)\b",
        ]
        return any(re.search(p, reply_lower) for p in patterns)

    elif field == "email":
        patterns = [
            r"\byour\s+(?:email|e-mail)\b",
            r"\bwhat(?:'s|\s+is)\s+.*?\b(?:email|e-mail)\b",
            r"\bshare\s+(?:your\s+)?(?:email|e-mail)\b",
            r"\b(?:share|provide)\s+.*?\b(?:email|e-mail)\b",
            r"\bbest\s+(?:email|e-mail)\s+to\s+reach\b",
            r"\bemail\s+(?:address|id)\b.*\?",
            r"\bsend\s+(?:you|it|details|info|proposal|brochure)\b.*\b(?:email|e-mail|inbox)\b",
            r"\b(?:email|e-mail)\b.*\bsend\b.*\?",
            r"\breach\s+you\b.*\b(?:email|e-mail)\b",
            r"\b(?:email|e-mail)\b.*\breach\s+you\b",
            r"\bcould\s+(?:you\s+)?(?:share|provide)\s+(?:your\s+)?(?:email|e-mail)\b",
            r"\b(?:could\s+(?:you\s+)?(?:also\s+|kindly\s+|please\s+)?|please\s+)?(?:share|provide)\s+.*?\b(?:email|e-mail)\b",
        ]
        return any(re.search(p, reply_lower) for p in patterns)

    elif field == "phone":
        patterns = [
            r"\byour\s+(?:phone|mobile|contact|cell)\s*(?:number)?\b.*\?",
            r"\bwhat(?:'s|\s+is)\s+.*?\b(?:phone|mobile|contact|cell)\s*(?:number)?\b",
            r"\bshare\s+(?:your\s+)?(?:phone|mobile|contact)\s*(?:number)?\b",
            r"\b(?:share|provide)\s+.*?\b(?:phone|mobile|contact|cell)\s*(?:number)?\b",
            r"\bgood\s+(?:number|phone)\s+to\s+(?:reach|call|contact)\b",
            r"\bgood\s+.*?(?:number|phone|mobile|contact)\s+to\s+(?:reach|call|contact)\b",
            r"\bcall\s+you\b.*\b(?:number|phone|mobile|contact)\b",
            r"\b(?:number|phone|mobile|contact)\b.*\bcall\s+you\b",
            r"\bour\s+(?:expert|team|consultant|sitecore\s+expert)\s+.*?\bcall\s+you\b",
            r"\bgive\s+(?:you|them)\s+a\s+call\b.*\b(?:number|phone)\b",
            r"\bgive\s+.*?\bcall\b.*\b(?:number|phone|mobile|contact)\b",
            r"\b(?:number|phone|mobile|contact)\b.*\bgive\s+.*?\bcall\b",
            r"\bwould\s+you\s+like\s+(?:a\s+)?call\b",
            r"\bcould\s+(?:you\s+)?(?:share|provide)\s+(?:your\s+)?(?:phone|mobile|contact)\b",
            r"\b(?:could\s+(?:you\s+)?(?:also\s+|kindly\s+|please\s+)?|please\s+)?(?:share|provide)\s+.*?\b(?:phone|mobile|contact|cell)\b",
            r"\bphone\s+number\s+where\s+we\s+can\s+reach\s+you\b",
        ]
        return any(re.search(p, reply_lower) for p in patterns)

    return False


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
    as well as determining the next expected input from the assistant's latest reply.
    Look for:
    - The user's name (e.g., "I'm Suma", "My name is John", or when the assistant addresses them by name)
    - Their company/organization (e.g., "I work at Pfizer", "We are from Google", "our company XYZ")
    - Email address
    - Phone number
    - What they are looking for (notes/requirements)
    - The expected input (which field the assistant is asking for in the latest ASSISTANT reply)

    Conversation:
    {conversation_text}

    Respond with ONLY a JSON object in this exact format, nothing else:
    {{"lead_name":"...","company_name":"...","email":"...","phone":"...","notes":"...","ready":true/false,"facts":[],"expected_input":"lead_name" | "company_name" | "email" | "phone" | null}}

    Rules:
    - Use "" for any field not mentioned in the conversation.
    - Set "ready" to true if BOTH lead_name and company_name are non-empty.
    - Only extract information that the USER explicitly stated. Do NOT guess or hallucinate.
    - Set "expected_input" to the field name that the assistant explicitly asked for in the latest ASSISTANT reply ("lead_name", "company_name", "email", "phone", or null).
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
        "phone": lead_state.phone,
        "notes": lead_state.notes,
    }

    return lead_data, lead_state.lead_saved, resolved_user_id

async def build_dynamic_prompt(
    db: AsyncSession,
    session_id: str,
    user_id: str,
    user_message: str
) -> tuple[str, List[Dict[str, Any]], List[Message]]:
    """Builds the fully context-enriched system prompt including RAG and user memories."""
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

    # 6. Count User Messages to set Mindful Talk turn rules
    stmt = select(Message).where(Message.conversation_id == session_id, Message.role == "user")
    user_msg_result = await db.execute(stmt)
    user_msg_count = len(user_msg_result.scalars().all()) + 1

    if user_msg_count <= settings.MINDFUL_TALK_TURNS:
        phase_instruction = (
            f"\n\n## CURRENT PHASE: MINDFUL TALK (Turn {user_msg_count})\n"
            "DO NOT ask for any lead/contact details (name, email, company, phone) yet. "
            "Focus completely on answering the user's questions and understanding their business requirements."
        )
    else:
        phase_instruction = (
            f"\n\n## CURRENT PHASE: LEAD COLLECTION (Turn {user_msg_count})\n"
            "You may now naturally and smartly start collecting lead details (starting with name, then company) "
            "woven into your replies. Remember to ask for only ONE detail at a time, provide value first, and never push."
        )

    # 7. Formulate current lead status
    lead_state = await LeadService.get_or_create_lead_state(db, session_id, user_id)
    lead_status_json = {
        "lead_name": lead_state.lead_name,
        "company_name": lead_state.company_name,
        "email": lead_state.email,
        "phone": lead_state.phone,
        "notes": lead_state.notes,
    }

    # 8. Assemble components
    prompt_builder = [SYSTEM_PROMPT]
    
    if conv.summary:
        prompt_builder.append(f"\n\n## Summary of Previous Messages\n{conv.summary}")
        
    prompt_builder.append(f"\n\n## User Profile (Episodic Memory)\n{episodic_text}")
    prompt_builder.append(f"\n\n## Retrieved Conversation Memory (Relevant Past Context)\n{memory_text}")
    prompt_builder.append(f"\n\n## Retrieved Website Knowledge\n{knowledge_text}")
    prompt_builder.append(f"\n\n## Current Lead Status (already collected)\n{json.dumps(lead_status_json)}")
    prompt_builder.append(phase_instruction)
    prompt_builder.append(
        "\n\nRemember: provide value first, then ask for the NEXT missing field if in the lead collection phase. "
        "Don't re-ask for fields already collected."
    )

    return "".join(prompt_builder), knowledge_results, recent_messages


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

    # Compile prompt and context
    system_prompt, knowledge_results, recent_messages = await build_dynamic_prompt(
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

    # Build sources info list
    sources = []
    seen_urls = set()
    for r in knowledge_results:
        if r["url"] not in seen_urls:
            sources.append(SourceInfo(title=r["title"], url=r["url"], score=round(r["score"], 3)))
            seen_urls.add(r["url"])

    # Map expected input
    expected_input_data = None
    if lead_json and lead_json.get("expected_input"):
        # Double check turn rules & ensure assistant reply actually asks for this field
        stmt = select(Message).where(Message.conversation_id == session_id, Message.role == "user")
        user_msg_result = await db.execute(stmt)
        user_msg_count = len(user_msg_result.scalars().all()) + 1
        
        if user_msg_count > settings.MINDFUL_TALK_TURNS:
            mapped = map_expected_input(lead_json["expected_input"])
            if mapped and validate_expected_input(mapped, clean_reply):
                expected_input_data = ExpectedInputSchema(**mapped)

    return ChatResponse(
        reply=clean_reply,
        session_id=session_id,
        user_id=resolved_user_id,
        sources=sources[:3],
        lead_collected=LeadStateSchema(**lead_data),
        lead_saved=lead_just_saved,
        expected_input=expected_input_data
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

    # Compile prompt and context
    system_prompt, knowledge_results, recent_messages = await build_dynamic_prompt(
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

        # Format sources list
        sources = []
        seen_urls = set()
        for r in knowledge_results:
            if r["url"] not in seen_urls:
                sources.append({"title": r["title"], "url": r["url"], "score": round(r["score"], 3)})
                seen_urls.add(r["url"])

        # Map expected input
        expected_input_data = None
        if lead_json and lead_json.get("expected_input"):
            # Double check turn rules & ensure assistant reply actually asks for this field
            stmt = select(Message).where(Message.conversation_id == session_id, Message.role == "user")
            user_msg_result = await db.execute(stmt)
            user_msg_count = len(user_msg_result.scalars().all()) + 1
            
            if user_msg_count > settings.MINDFUL_TALK_TURNS:
                mapped = map_expected_input(lead_json["expected_input"])
                if mapped and validate_expected_input(mapped, clean_reply):
                    expected_input_data = mapped

        # Send final metadata event (use resolved_user_id so frontend updates its stored identity)
        metadata = {
            "type": "metadata",
            "session_id": session_id,
            "user_id": resolved_user_id,
            "sources": sources[:3],
            "lead": lead_data,
            "lead_saved": lead_just_saved,
            "expected_input": expected_input_data
        }
        yield f"data: {json.dumps(metadata)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
