import json
import logging
import re
import uuid
import asyncio
import textwrap
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, Depends, BackgroundTasks
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings
from app.database import get_db, AsyncSessionLocal
from app.models import Message, LeadState
from app.schemas import ChatRequest, ChatResponse, SourceInfo, CaseStudyInfo, LeadStateSchema
from app.services.knowledge import KnowledgeService
from app.services.memory import MemoryService
from app.services.lead import LeadService
from app.services.llm import call_deepseek, stream_deepseek
from app.services.user_identity import UserIdentityService
from app.services.embedder import EmbedderService

logger = logging.getLogger("biztechbot")

router = APIRouter(prefix="/api", tags=["Chat"])

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

SYSTEM_PROMPT = textwrap.dedent("""\
You are BizBot — the AI sales assistant for Biztechnosys, a Sitecore Gold Partner \
and enterprise digital experience engineering company.

## Knowledge Grounding (STRICT)
All factual information about Biztechnosys — including office locations, addresses, contact details, \
leadership, team size, company track record, services, technologies, solutions, and case studies — is \
retrieved dynamically from the database and provided in the "Retrieved Website Knowledge" and \
"Relevant Case Studies" sections below.
- You MUST answer all factual questions using ONLY the retrieved website knowledge.
- ZERO HALLUCINATIONS: Do NOT invent, assume, or hardcode office locations, countries, services, pricing, \
timelines, or team members.
- Biztechnosys only has offices in the locations explicitly confirmed in the retrieved website knowledge. \
Never claim or assume offices in any other country or city.

## Your Mission
Execute a **focused 4-step conversation** to understand the visitor's needs, recommend the right \
solution, and collect their contact details — with ZERO wasted turns.

## Conversation Flow (STRICT — follow these phases in order)

### Phase 1 — PROJECT DISCOVERY (Your first response)
- If the visitor greets or states general intent, use ONE warm line, then ask what project or business challenge brought them here.
- If the visitor asks a specific question about Biztechnosys, Sitecore, solutions, case studies, leadership, team members, or technologies, ANSWER IT WISELY AND CONCISELY first using ONLY the retrieved website knowledge (ZERO hallucinations), and then smoothly ask: "**What project or business challenge are you looking to solve?**"
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
business outcomes in 1-2 brief sentences and provide the exact link.
- **CRITICAL**: If no matching case study is provided in context, do NOT invent one. \
Only use what is explicitly provided.
- After your recommendation, naturally transition to asking them for their contact details conversationally: \
"**Could you please share your name and business email so our team can prepare a tailored proposal?**"

### IMPORTANT — Services & Case Studies Rules
- **Do NOT list services unprompted.** Only mention specific services when the user explicitly asks \
about services, solutions, or what Biztechnosys offers. During normal conversation, focus on \
understanding their needs — do NOT dump a list of services.
- **Case studies only when relevant:** Only reference case studies when the user is asking about \
a specific service/solution or when you are making a recommendation in Phase 3. Do NOT proactively \
show case studies during greetings, general questions, or Phase 1/2 conversations.

### Phase 4 — DIRECT APPOINTMENT BOOKING & CONTACT WITH SALES
- When the user asks to connect, get in touch with sales or support, have a call, or book an appointment (e.g., "How do I get in touch with sales/support?", "I want to connect call", "book appointment with sales", "reach sales"):
  - Provide direct contact channels (**Phone:** +91 8035827097 | **Email:** info@biztechnosys.com) AND immediately offer to connect them directly for a 30-minute discovery call with our sales consultant!
  - If their email/contact info is already collected: Warmly reassure them: "**Great! I have scheduled your connect request directly with our sales team. Our sales specialist will reach out to you directly.**" Ask what specific goals or requirements they would like to review on the call.
  - If their email is not yet collected: Politely ask for their name and business email so our sales consultant can send over calendar invite slots directly.
  - **EMAIL PRIVACY (STRICT)**: NEVER print, display, or repeat the user's email address or registered contact details in your conversational response. Never write the user's email ID in your text.
  - Set "expected_input" to null.

### Post-Lead Rules (STRICT):
- CRITICAL: If the user asks ANY informational, technical, or exploratory question (about services, technologies, tech stacks, implementation phases, capabilities, benefits, case studies, or how platforms work), ALWAYS answer their specific question DIRECTLY, CONCISELY, and ACCURATELY using ONLY the retrieved website knowledge.
- Understand the user's specific question carefully. Provide a concise, clear, and wise answer (2-3 short paragraphs or 3-4 bullet points maximum) directly answering what was asked. Never dump excessive text or entire web pages.
- Do NOT deflect standard technical or website questions or say 'our experts will discuss it during the scheduled call' instead of answering. Answer concisely first, and end with a natural, varied closing or relevant question. Avoid repeating the exact same phrase on every turn.
- If the user mentions a **NEW requirement or different project**, treat it as a fresh request: recommend the appropriate service/product, include a case study if available, and UPDATE the "notes" field in your JSON block with the new requirement.
- If the user says no or wraps up, say goodbye warmly and end the conversation.

## Lead JSON Status Block (MANDATORY — EVERY response)
After EVERY response, output this JSON block at the very end on its own line:
```json
{"lead_name":"...","email":"...","appointment_date":"...","phone":"...","company_name":"...","country":"...","notes":"...","ready":true/false,"facts":["..."],"expected_input":null}
```

Rules:
- Fill fields with whatever the user has shared so far. Use "" for uncollected fields.
- "notes": Brief summary of the user's project/requirements. If user requests a call/appointment with sales, include "Requested sales appointment". Update if their requirements change.
- "ready": true when BOTH lead_name AND company_name are non-empty.
- "facts": 1-3 new facts learned this turn.
- "expected_input": Always null. All interactions are handled directly in conversational chat.
- ALWAYS carry forward previously collected info — never blank out known fields.
- **NEVER populate or mention "appointment_date" in your reply unless the Current Phase block \
explicitly states the user has a confirmed appointment. Appointment details come ONLY from \
the CRM/booking database, never from the website knowledge base.**

## Knowledge Base & Anti-Hallucination Rules (STRICT)
Answer strictly from the context provided below ("Retrieved Website Knowledge" and "Relevant Case Studies" sections). Follow these strict rules:
- **ZERO HALLUCINATION POLICY**: Never invent, extrapolate, or assume ANY services, products, pricing, cost estimates, timelines, delivery dates, SLAs, or office locations that are not explicitly present in the retrieved context.
- **CONCISE & DIRECT**: Answer the user's specific question directly. Summarize key points crisply without dumping raw text from the context.
- **PRICING & TIMELINES**: Never state or imply any price, cost, or delivery timeline unless \
it is verbatim in the retrieved context. If not present, say: "Our team will share exact \
pricing and timeline details during the discovery call."
- **APPOINTMENTS**: Never mention or invent appointment dates/times from knowledge base context. \
Appointment info is supplied only by the booking system and will appear in the Current Phase block.
- Ground every factual statement directly in the retrieved website text from the database.
- **KNOWLEDGE PRECEDENCE**: Always prioritize facts in "Retrieved Website Knowledge" over past conversation history or prior refusals. If the retrieved website knowledge contains the answer (such as office locations, addresses, leadership names, roles, services, or technical details), use it directly even if earlier messages in the conversation history stated the information was not available.
- If the retrieved context does not contain enough information to answer a question, truthfully say: \
"I'd love to help with that! Let me connect you with one of our experts who can provide the exact details." Do NOT guess or fabricate an answer.
- When referencing case studies, ONLY use ones from the "Relevant Case Studies" section. If none are provided, do NOT mention any case study.
- When listing services or capabilities, use ONLY what appears in the context.

## Response Style
- **Be CONCISE above all else.** Answer exactly what the user asked — nothing more, nothing less.
- Keep answers short: 1-2 short paragraphs or 2-4 bullet points maximum. Avoid verbose explanations.
- Do NOT list all services, technologies, or capabilities unless the user specifically asks for a list.
- Answer the user's specific question directly. If they ask about one thing, answer that one thing.
- Never output walls of text. Be crisp, insightful, and easy to read.
- NEVER repeatedly append appointment reminders or "during your Discovery Call" to every answer.
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
    conversation_lines = []
    for m in recent_messages:
        role_label = "USER" if m.role == "user" else "ASSISTANT"
        conversation_lines.append(f"{role_label}: {m.content}")
    conversation_lines.append(f"USER: {current_user_message}")
    conversation_lines.append(f"ASSISTANT: {current_assistant_reply}")
    conversation_text = "\n".join(conversation_lines)

    extraction_prompt = textwrap.dedent(f"""\
    Analyze the following conversation and extract any lead/contact information mentioned by the USER.
    Look for:
    - The user's name (e.g., "I'm Suma", "My name is John", or when the assistant addresses them by name)
    - Their company/organization (e.g., "I work at Pfizer", "We are from Google", "our company XYZ")
    - Email address
    - Appointment date (in DD-MM-YYYY format)
    - Phone number
    - Country
    - What they are looking for (notes/requirements)
    - Whether the assistant is inviting them to book an appointment (expected_input)

    Conversation:
    {conversation_text}

    Respond with ONLY a JSON object in this exact format, nothing else:
    {{"lead_name":"...","company_name":"...","email":"...","appointment_date":"...","phone":"...","country":"...","notes":"...","ready":true/false,"facts":[],"expected_input":"booking_form" | null}}

    Rules:
    - Use "" for any field not mentioned in the conversation.
    - Set "ready" to true if BOTH lead_name and company_name are non-empty.
    - Only extract information that the USER explicitly stated. Do NOT guess or hallucinate.
    - Set "expected_input" to "booking_form" if the assistant is asking the user to schedule or book a discovery call. Otherwise set it to null.
    """)

    try:
        # Use temperature=0.0 for extraction to prevent fabrication
        import httpx as _httpx
        async with _httpx.AsyncClient(timeout=60) as _client:
            _resp = await _client.post(
                f"{settings.DEEPSEEK_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "deepseek-chat",
                    "messages": [
                        {"role": "system", "content": "You are a precise data extraction assistant. Output only valid JSON. Extract ONLY information the USER explicitly stated. Do NOT guess or invent any details."},
                        {"role": "user", "content": extraction_prompt}
                    ],
                    "temperature": 0.0,
                    "max_tokens": 512,
                    "stream": False,
                },
            )
            _resp.raise_for_status()
            raw = _resp.json()["choices"][0]["message"]["content"]
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
    # 1. Strip markdown code fence containing lead_name
    text = re.sub(r'```json\s*\n?\s*\{.*?"lead_name".*?\}\s*\n?\s*```', '', text, flags=re.DOTALL)
    
    # 2. Strip raw JSON block starting with {"lead_name"
    for match in list(re.finditer(r'\{\s*"lead_name"', text)):
        json_str = _extract_json_by_braces(text, match.start())
        if json_str:
            text = text.replace(json_str, "")
            
    # 3. Fallback regexes
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
                    user_id = resolved_user_id
            except Exception as e:
                logger.error(f"Error resolving user identity: {e}", exc_info=True)
                await db.rollback()

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

    # Pre-compute query embedding ONCE to reuse across all vector searches (eliminates duplicate encodings)
    query_emb = await EmbedderService.encode_single_async(user_message)

    # 3. Retrieve User Episodic Memory summaries
    episodic_results = await MemoryService.search_episodic_memories(
        db, user_id, user_message, query_embedding=query_emb
    )
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
        threshold=settings.SIMILARITY_THRESHOLD_MEMORY,
        query_embedding=query_emb,
    )
    memory_text = "\n".join(
        [f"- [{m['role'].upper()}]: {m['content']}" for m in memory_results]
    ) if memory_results else "No relevant past conversation memories found."

    # 5. Retrieve Website Knowledge using vector search + cross-encoder rerank.
    # Contextualize query with recent conversation topics for follow-up questions
    # (e.g. "what are the six phases?", "how long does it take?", "write about the tech stack").
    search_query = user_message
    if recent_messages:
        # Collect recent user and assistant topics
        recent_topics = []
        for m in reversed(recent_messages):
            if m.role == "user" and m.content != user_message:
                recent_topics.append(m.content)
            elif m.role == "assistant":
                first_line = m.content.split("\n")[0].replace("**", "").replace("##", "").strip()
                if first_line and len(first_line) > 10 and first_line not in recent_topics:
                    recent_topics.append(first_line[:120])
        
        # Check if query is genuinely an anaphoric follow-up lacking a clear subject
        # e.g., "what are the six phases?", "how long does it take?", "what are its benefits?", "tell me more"
        anaphora_pattern = r'\b(it|this|that|these|those|its|their|the phases|the steps|the stack|the process|how long|timeline|tell me more|more details)\b'
        has_anaphora = bool(re.search(anaphora_pattern, user_message.lower()))

        # If user explicitly specifies a subject/entity, it is NOT an anaphoric follow-up
        has_specific_subject = any(kw in user_message.lower() for kw in [
            "who is", "who are", "what is", "where is", "how is", "how does", "how many", "tell me about", "leadership", "team",
            "practice head", "cto", "ceo", "founder", "coo", "director", "uday", "kalpesh",
            "manish", "ramachandran", "xm cloud", "content hub", "ordercloud", "cdp",
            "personalize", "send", "search & rec", "discovery & assessment", "biztechnosys",
            "privacy", "policy", "account", "affiliate", "tagline", "positioning", "response", "enquir"
        ])

        is_followup = has_anaphora and not has_specific_subject
        if is_followup and recent_topics:
            search_query = f"{user_message} {recent_topics[0]}"

    knowledge_emb = query_emb if search_query == user_message else await EmbedderService.encode_single_async(search_query)

    knowledge_results = await KnowledgeService.search_knowledge(
        db,
        query=search_query,
        candidate_k=settings.TOP_K_KNOWLEDGE,
        final_k=settings.TOP_K_RERANKED,
        threshold=settings.SIMILARITY_THRESHOLD_KNOWLEDGE,
        query_embedding=knowledge_emb,
    )
    knowledge_text = "\n\n---\n\n".join(
        [f"[Chunk {i+1} — Source: {k['title']}]\n{k['text']}\nLink: {k['url']}" for i, k in enumerate(knowledge_results)]
    ) if knowledge_results else "No specific website knowledge found."

    # 6. Count User Messages to determine conversation phase
    stmt = select(Message).where(Message.conversation_id == session_id, Message.role == "user")
    user_msg_result = await db.execute(stmt)
    user_msg_count = len(user_msg_result.scalars().all()) + 1

    # 7. Formulate current lead status
    lead_state = await LeadService.get_or_create_lead_state(db, session_id, user_id)
    lead_status_json = {
        "lead_name": lead_state.lead_name,
        "company_name": lead_state.company_name,
        "email": lead_state.email,
        "phone": lead_state.phone,
        "country": lead_state.country,
        "notes": lead_state.notes,
    }

    # 8. Determine phase based on state in DB and conversation history
    has_recommendation = False
    # Use specific recommendation keywords — generic terms like "biztechnosys" or "sitecore"
    # appear in almost every response and cause premature phase transitions
    recommendation_keywords = ["recommend", "best-fit", "propose", "suggest", "ideal solution", "perfect fit"]
    for msg in recent_messages:
        if msg.role == "assistant" and any(keyword in msg.content.lower() for keyword in recommendation_keywords):
            has_recommendation = True

    # 9. Retrieve Relevant Case Studies — ONLY when user asks about services/solutions
    #    This avoids injecting case studies into greetings and general questions.
    service_keywords = [
        "service", "solution", "offer", "provide", "capability", "what do you do",
        "sitecore", "xm cloud", "content hub", "ordercloud", "cdp", "personalize",
        "send", "search", "migration", "implementation", "development", "build",
        "upgrade", "integrate", "case study", "case studies", "portfolio", "project",
        "example", "success story", "recommend", "suggest", "best fit", "proposal",
    ]
    user_asks_about_services = any(kw in user_message.lower() for kw in service_keywords)
    # Also retrieve case studies during active recommendation phase (Phase 3)
    # BUT only when lead is NOT yet saved — returning users (lead_saved=True)
    # should only get case studies when they explicitly ask about services.
    is_recommendation_phase = (
        not lead_state.lead_saved
        and has_recommendation
        and user_msg_count >= 3
        and bool(lead_state.lead_name)
    )

    case_study_results = []
    if user_asks_about_services or is_recommendation_phase:
        case_study_results = await KnowledgeService.search_case_studies(
            db,
            query=search_query,
            candidate_k=6,
            final_k=2,
            threshold=0.25,
            query_embedding=knowledge_emb,
        )
    case_study_text = "\n\n---\n\n".join(
        [f"[Case Study: {cs['title']}]\n{cs['text']}\nRead more: {cs['url']}" for cs in case_study_results]
    ) if case_study_results else "No relevant case studies found."

    # Appointment details are sourced strictly from CRM/DB state, never hallucinated from knowledge base
    if lead_state.appointment_date:
        phase_instruction = (
            f"\n\n## CURRENT PHASE: POST-LEAD & BOOKED (Turn {user_msg_count})\n"
            "The user has already submitted contact details and booked an appointment.\n\n"
            "⚠️ **APPOINTMENT SUPPRESSION RULE (MANDATORY):**\n"
            "You MUST NOT mention any appointment date, time, Discovery Call, or booking details "
            "in your response. IGNORE any appointment references you see in the conversation history. "
            "The ONLY exception is if the user's CURRENT message explicitly asks about their "
            "appointment (e.g. 'When is my call?', 'What time is my appointment?'). "
            f"Only then, respond with: {lead_state.appointment_date}.\n\n"
            "**Response rules:**\n"
            "- For greetings ('Hi', 'Hello'): Give a SHORT warm greeting (1 line) and ask "
            "if they have any questions or a new project. Nothing else.\n"
            "- For questions: Answer DIRECTLY and CONCISELY using retrieved website knowledge.\n"
            "- For new requirements: Recommend the best-fit service and update 'notes'.\n"
            "- For goodbye: Say goodbye warmly in 1 line."
        )
    elif lead_state.lead_saved:
        phase_instruction = (
            f"\n\n## CURRENT PHASE: SALES APPOINTMENT BOOKING & POST-LEAD (Turn {user_msg_count})\n"
            "The lead contact details are already captured and saved in ERP/CRM.\n\n"
            "**Rules for this phase:**\n"
            "1. If the user asks to connect, have a call, or book an appointment with sales (e.g., 'I want to connect call', 'connect call', 'book a call', 'talk with sales'):\n"
            "   - DIRECTLY confirm: \"Great! I have booked your appointment request directly with our sales team. Our sales specialist will reach out to you directly to coordinate and conduct the call.\"\n"
            "   - Reassure them that everything is set, and ask if there are any specific questions or project details they want the sales team to review ahead of the call.\n"
            "   - PRIVACY RULE (STRICT): NEVER print, display, or mention the user's email address in your response.\n"
            "   - NEVER mention any form, inline form, or calendar slot picker. Set expected_input to null.\n"
            "   - Update the 'notes' field in your JSON block to: 'Requested sales appointment'.\n"
            "2. If the user asks ANY informational or technical questions about Biztechnosys, services, Sitecore, or case studies:\n"
            "   - Answer directly and concisely using ONLY retrieved website knowledge.\n"
            "3. If the user says goodbye or wraps up: Thank them warmly and conclude gracefully.\n"
        )
    elif has_recommendation:
        phase_instruction = (
            f"\n\n## CURRENT PHASE: LEAD CAPTURE (Turn {user_msg_count})\n"
            "CRITICAL INSTRUCTION: If the user asks ANY informational, leadership, or technical question "
            "(such as 'Who is the Sitecore Practice Head?', 'Who is the CTO?', services, capabilities, or solutions), "
            "you MUST answer their specific question DIRECTLY, CONCISELY, and ACCURATELY first using ONLY the retrieved website knowledge. "
            "Do NOT ask for contact details when answering a factual or informational question. Set expected_input to null.\n\n"
            "Otherwise, if the user is answering qualifying questions or continuing project discussion, "
            "politely ask if they would like to share their name and business email so our team can prepare a tailored proposal."
        )
    elif user_msg_count <= 1:
        phase_instruction = (
            f"\n\n## CURRENT PHASE: PROJECT DISCOVERY (Turn {user_msg_count})\n"
            "This is the first response. If the user asked an informational or technical question about Biztechnosys, "
            "Sitecore, solutions, case studies, or services, ANSWER IT WISELY AND CONCISELY (2-3 short paragraphs or 3-4 bullet points) "
            "first using ONLY the retrieved website knowledge with ZERO hallucinations. Then ask what project or business challenge brought them here.\n"
            "Do NOT ask for contact details or recommend products yet."
        )
    else:
        phase_instruction = (
            f"\n\n## CURRENT PHASE: PRE-SALES QUALIFYING & ANALYSIS (Turn {user_msg_count})\n"
            "CRITICAL: If the user asked an informational, technical, or exploratory question about any Biztechnosys service, "
            "solution, technology, implementation phase, timeline, or case study, ALWAYS PROVIDE A DIRECT, CONCISE, AND WISE "
            "ANSWER FIRST using ONLY the retrieved website knowledge. Directly address their specific question in 2-3 short paragraphs "
            "or 3-4 clear bullet points without dumping excessive text.\n"
            "Then analyze the user's requirements. If additional information is needed, ask 1-2 intelligent "
            "pre-sales qualification questions to better understand their specific needs.\n"
            "Once you have sufficient context to make a recommendation, transition immediately to the recommendation step:\n"
            "1. Recommends the most suitable Biztechnosys service(s) or product(s).\n"
            "2. Explains why it fits in 2-3 concise bullet points.\n"
            "3. Shares relevant case studies if available in context.\n"
            "4. Politely asks the user for their name and business email so the team can prepare a tailored proposal."
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

    # 11. Final anti-hallucination reinforcement (placed LAST so the LLM sees it
    #     right before generating — positional recency bias makes this critical)
    prompt_builder.append(textwrap.dedent("""

    ## ⚠️ CRITICAL REMINDER — ZERO HALLUCINATION
    Before you write your response, re-read the "Retrieved Website Knowledge" and "Relevant Case Studies" sections above.
    - Answer ONLY from those sections. Every factual claim must be traceable to those sections.
    - If the information is NOT explicitly present in those sections, say: "I'd love to help with that! Let me connect you with one of our experts who can provide the exact details."
    - Do NOT invent, extrapolate, or assume ANY services, pricing, timelines, case studies, phases, team members, or technical details.
    - Do NOT mention any appointment date unless it appears in the "CURRENT PHASE" section above.
    """))

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
                lead_name="",
                email="",
                company_name="",
                notes=""
            )
            db.add(lead_state)
            await db.commit()

        # 2. VALIDATE EMAIL before creating appointment
        email_valid, email_reason = is_valid_booking_email(lead_state.email)
        if not email_valid:
            error_msg = (
                "⚠️ **We need your email address before booking.**\n\n"
                "To schedule a Discovery Call, please share your **email address** "
                "so we can send you the calendar invite and confirmation.\n\n"
                "**Could you please provide your email address?**"
            )
            logger.warning(
                f"Booking blocked for session {session_id}: email validation failed "
                f"(reason={email_reason}, email='{lead_state.email}')"
            )
            # Store messages so the conversation context is preserved
            await MemoryService.store_message_and_embed(db, session_id, user_id, "user", req.message)
            await MemoryService.store_message_and_embed(db, session_id, user_id, "assistant", error_msg)

            lead_data = {
                "lead_name": lead_state.lead_name or "",
                "company_name": lead_state.company_name or "",
                "email": lead_state.email or "",
                "appointment_date": "",
                "phone": lead_state.phone or "",
                "country": lead_state.country or "",
                "notes": lead_state.notes or "",
            }
            return ChatResponse(
                reply=error_msg,
                session_id=session_id,
                user_id=user_id,
                sources=[],
                case_studies=[],
                lead_collected=LeadStateSchema(**lead_data),
                lead_saved=lead_state.lead_saved,
                lead_form=None,
                booking_form=None,
                conversation_complete=False
            )

        lead_name = lead_state.lead_name or ""
        user_email = lead_state.email
        company_name = lead_state.company_name or ""
        notes = lead_state.notes or ""
        
        formatted_appointment = f"{booking_date} {booking_time}"
        lead_state.appointment_date = formatted_appointment
        
        from app.services.outlook import OutlookService
        calendar_success = await OutlookService.create_calendar_event(
            lead_name=lead_name,
            user_email=user_email,
            company_name=company_name,
            date_str=booking_date,
            time_str=booking_time,
            notes=notes
        )
        
        email_success = await OutlookService.send_confirmation_emails(
            lead_name=lead_name,
            user_email=user_email,
            company_name=company_name,
            date_str=booking_date,
            time_str=booking_time,
            notes=notes
        )
        
        await db.commit()
        try:
            await LeadService.sync_lead_to_erpnext(db, lead_state, newly_filled=True)
        except Exception as e:
            logger.error(f"Error syncing appointment date to ERPNext: {e}")
            
        confirmation_msg = (
            f"🎉 **Discovery Call Confirmed!**\n\n"
            f"Your appointment with the Biztechnosys team is scheduled for **{booking_date} at {booking_time}** (IST).\n\n"
            f"A calendar invitation and confirmation email have been sent to **{user_email}** and **{settings.OUTLOOK_EMAIL}**.\n\n"
            f"**Do you have any other project or requirement we can help you with?**"
        )
        
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

    # Retrieve current lead state
    lead_state = await LeadService.get_or_create_lead_state(db, session_id, resolved_user_id)

    # Build sources info list
    sources = []
    seen_urls = set()
    for r in knowledge_results:
        if r["url"] not in seen_urls:
            sources.append(SourceInfo(title=r["title"], url=r["url"], score=round(r["score"], 3)))
            seen_urls.add(r["url"])


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
        lead_form=None,
        booking_form=None,
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
                    lead_name="",
                    email="",
                    company_name="",
                    notes=""
                )
                db.add(lead_state)
                await db.commit()

            # VALIDATE EMAIL before creating appointment
            email_valid, email_reason = is_valid_booking_email(lead_state.email)
            if not email_valid:
                error_msg = (
                    "⚠️ **We need your email address before booking.**\n\n"
                    "To schedule a Discovery Call, please share your **email address** "
                    "so we can send you the calendar invite and confirmation.\n\n"
                    "**Could you please provide your email address?**"
                )
                logger.warning(
                    f"Booking blocked (stream) for session {session_id}: email validation failed "
                    f"(reason={email_reason}, email='{lead_state.email}')"
                )
                await MemoryService.store_message_and_embed(db, session_id, user_id, "user", req.message)
                await MemoryService.store_message_and_embed(db, session_id, user_id, "assistant", error_msg)

                lead_data = {
                    "lead_name": lead_state.lead_name or "",
                    "company_name": lead_state.company_name or "",
                    "email": lead_state.email or "",
                    "appointment_date": "",
                    "phone": lead_state.phone or "",
                    "country": lead_state.country or "",
                    "notes": lead_state.notes or "",
                }
                chunk_size = 20
                for i in range(0, len(error_msg), chunk_size):
                    chunk = error_msg[i:i+chunk_size]
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
                    "conversation_complete": False
                }
                yield f"data: {json.dumps(metadata)}\n\n"
                return

            lead_name = lead_state.lead_name or ""
            user_email = lead_state.email
            company_name = lead_state.company_name or ""
            notes = lead_state.notes or ""
            
            formatted_appointment = f"{booking_date} {booking_time}"
            lead_state.appointment_date = formatted_appointment
            
            from app.services.outlook import OutlookService
            calendar_success = await OutlookService.create_calendar_event(
                lead_name=lead_name,
                user_email=user_email,
                company_name=company_name,
                date_str=booking_date,
                time_str=booking_time,
                notes=notes
            )
            
            email_success = await OutlookService.send_confirmation_emails(
                lead_name=lead_name,
                user_email=user_email,
                company_name=company_name,
                date_str=booking_date,
                time_str=booking_time,
                notes=notes
            )
            
            await db.commit()
            try:
                await LeadService.sync_lead_to_erpnext(db, lead_state, newly_filled=True)
            except Exception as e:
                logger.error(f"Error syncing appointment date to ERPNext: {e}")
                
            confirmation_msg = (
                f"🎉 **Discovery Call Confirmed!**\n\n"
                f"Your appointment with the Biztechnosys team is scheduled for **{booking_date} at {booking_time}** (IST).\n\n"
                f"A calendar invitation and confirmation email have been sent to **{user_email}** and **{settings.OUTLOOK_EMAIL}**.\n\n"
                f"**Do you have any other project or requirement we can help you with?**"
            )
            
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
        
        try:
            async with stream_deepseek(llm_messages) as response:
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

        lead_state = await LeadService.get_or_create_lead_state(db, session_id, resolved_user_id)

        # Format sources list
        sources = []
        seen_urls = set()
        for r in knowledge_results:
            if r["url"] not in seen_urls:
                sources.append({"title": r["title"], "url": r["url"], "score": round(r["score"], 3)})
                seen_urls.add(r["url"])

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
        metadata = {
            "type": "metadata",
            "session_id": session_id,
            "user_id": resolved_user_id,
            "sources": sources[:3],
            "case_studies": case_studies_list,
            "lead": lead_data,
            "lead_saved": lead_just_saved,
            "lead_form": None,
            "booking_form": None,
            "conversation_complete": lead_just_saved and bool(lead_state.appointment_date)
        }
        yield f"data: {json.dumps(metadata)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
