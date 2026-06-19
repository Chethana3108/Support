from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

class ChatRequest(BaseModel):
    message: str = Field(..., description="The user's query or conversational message.")
    session_id: Optional[str] = Field(None, description="The session (conversation) identifier to persist history.")
    user_id: Optional[str] = Field(None, description="The user identifier to bind memories across sessions.")

class SourceInfo(BaseModel):
    title: str
    url: str
    score: float

class LeadStateSchema(BaseModel):
    lead_name: str = ""
    company_name: str = ""
    email: str = ""
    phone: str = ""
    notes: str = ""

class ChatResponse(BaseModel):
    reply: str
    session_id: str
    user_id: str
    sources: List[SourceInfo] = []
    lead_collected: LeadStateSchema
    lead_saved: bool = False
