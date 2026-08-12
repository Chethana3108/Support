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

class CaseStudyInfo(BaseModel):
    title: str
    url: str
    summary: str
    score: float

class LeadStateSchema(BaseModel):
    lead_name: str = ""
    company_name: str = ""
    email: str = ""
    appointment_date: str = ""
    phone: str = ""
    country: str = ""
    notes: str = ""

class LeadFormField(BaseModel):
    field: str
    input_type: str
    label: str
    placeholder: str
    required: bool

class LeadFormSchema(BaseModel):
    fields: List[LeadFormField]

class BookingSlotResponse(BaseModel):
    date: str
    slots: List[str]
    message: str

class BookingRequest(BaseModel):
    session_id: str
    appointment_date: str = Field(..., description="Date in YYYY-MM-DD format")
    appointment_time: str = Field(..., description="Selected time slot e.g. 03:30 PM")

class BookingResponse(BaseModel):
    success: bool
    message: str
    appointment_date: str
    appointment_time: str

class ChatResponse(BaseModel):
    reply: str
    session_id: str
    user_id: str
    sources: List[SourceInfo] = []
    case_studies: List[CaseStudyInfo] = []
    lead_collected: LeadStateSchema
    lead_saved: bool = False
    lead_form: Optional[LeadFormSchema] = None
    booking_form: Optional[Dict[str, Any]] = None
    conversation_complete: bool = False



