from pydantic import BaseModel, Field
from typing import Optional
from uuid import UUID


class ShopRegister(BaseModel):
    owner_name: str = Field(..., min_length=1, max_length=100)
    phone_number: str = Field(..., pattern=r"^\+92\d{10}$")
    pin: str = Field(..., min_length=4, max_length=6, pattern=r"^\d+$")


class ShopLogin(BaseModel):
    phone_number: str = Field(..., pattern=r"^\+92\d{10}$")
    pin: str = Field(..., min_length=4, max_length=6, pattern=r"^\d+$")


class TokenRefresh(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class ShopResponse(BaseModel):
    shop_id: UUID
    access_token: str
    refresh_token: str


class ShopInfoResponse(BaseModel):
    shop_id: UUID
    owner_name: str
    phone_number: str


class ClearKhataRequest(BaseModel):
    confirmed: bool = False


class ClearKhataResponse(BaseModel):
    status: str
    message: str
    cleared_entries_count: int



class CustomerResponse(BaseModel):
    id: UUID
    name: str
    balance: float


class BalanceResponse(BaseModel):
    customer_id: UUID
    customer_name: str
    balance: float


class SummaryResponse(BaseModel):
    total_outstanding: float
    customer_count: int


class VoiceProcessResponse(BaseModel):
    transcript: str
    intent: str
    requires_confirmation: bool
    pending_action_id: Optional[UUID] = None
    response_text: str
    response_audio_url: Optional[str] = None
    resolved_entities: dict
    ledger_updated: bool = False


class VoiceConfirmRequest(BaseModel):
    pending_action_id: UUID
    confirmed: bool


class VoiceConfirmResponse(BaseModel):
    status: str
    response_text: str
    response_audio_url: Optional[str] = None
    ledger_entry_id: Optional[UUID] = None
    ledger_updated: bool = False



class EntryResponse(BaseModel):
    id: UUID
    amount: float
    entry_type: str
    description: Optional[str] = None
    created_at: str


class CreateEntryRequest(BaseModel):
    customer_id: UUID
    amount: float
    entry_type: str
    description: Optional[str] = None
    confirmed: bool = False


