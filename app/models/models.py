import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Numeric, DateTime, ForeignKey, Text, Integer, Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
import enum

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class EntryType(str, enum.Enum):
    udhaar = "udhaar"
    payment = "payment"


class EntryStatus(str, enum.Enum):
    pending_confirmation = "pending_confirmation"
    confirmed = "confirmed"
    cancelled = "cancelled"


class ActionStatus(str, enum.Enum):
    pending = "pending"
    confirmed = "confirmed"
    cancelled = "cancelled"
    expired = "expired"


class Shop(Base):
    __tablename__ = "shops"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_name = Column(String, nullable=False)
    phone_number = Column(String, unique=True, nullable=False)
    pin_hash = Column(String, nullable=False)
    unusual_amount_threshold = Column(Numeric(12, 2), default=5000.00)
    stt_confidence_threshold = Column(Numeric(3, 2), default=0.70)
    failed_login_count = Column(Integer, default=0, nullable=False)
    locked_until = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    customers = relationship("Customer", back_populates="shop", cascade="all, delete-orphan")
    refresh_tokens = relationship("RefreshToken", back_populates="shop", cascade="all, delete-orphan")
    pending_actions = relationship("PendingAction", back_populates="shop", cascade="all, delete-orphan")


class Customer(Base):
    __tablename__ = "customers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shop_id = Column(UUID(as_uuid=True), ForeignKey("shops.id", ondelete="CASCADE"), nullable=False)
    name = Column(String, nullable=False)
    phone_number = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    shop = relationship("Shop", back_populates="customers")
    ledger_entries = relationship("LedgerEntry", back_populates="customer", cascade="all, delete-orphan")


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shop_id = Column(UUID(as_uuid=True), ForeignKey("shops.id", ondelete="CASCADE"), nullable=False)
    customer_id = Column(UUID(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    entry_type = Column(SAEnum(EntryType), nullable=False)
    status = Column(SAEnum(EntryStatus), default=EntryStatus.confirmed, nullable=False)
    raw_transcript = Column(Text, nullable=True)
    stt_confidence = Column(Numeric(3, 2), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)

    customer = relationship("Customer", back_populates="ledger_entries")


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shop_id = Column(UUID(as_uuid=True), ForeignKey("shops.id", ondelete="CASCADE"), nullable=False)
    token_hash = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)

    shop = relationship("Shop", back_populates="refresh_tokens")


class PendingAction(Base):
    __tablename__ = "pending_actions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shop_id = Column(UUID(as_uuid=True), ForeignKey("shops.id", ondelete="CASCADE"), nullable=False)
    session_id = Column(String, nullable=False)
    intent = Column(String, nullable=False)
    payload = Column(Text, nullable=False)  # JSONB stored as text for simplicity
    reason = Column(String, nullable=False)
    status = Column(SAEnum(ActionStatus), default=ActionStatus.pending, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    resolved_at = Column(DateTime(timezone=True), nullable=True)

    shop = relationship("Shop", back_populates="pending_actions")
