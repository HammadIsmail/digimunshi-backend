from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, update, case as sa_case
from uuid import UUID

from app.core.database import get_db
from app.core.deps import get_current_shop
from app.models.models import Shop, Customer, LedgerEntry, EntryType, EntryStatus
from app.schemas.schemas import CustomerResponse, BalanceResponse, SummaryResponse, EntryResponse, ClearKhataRequest, ClearKhataResponse, CreateEntryRequest


router = APIRouter(tags=["ledger"])


def balance_expression():
    return func.coalesce(
        func.sum(
            sa_case(
                (LedgerEntry.entry_type == EntryType.udhaar, LedgerEntry.amount),
                else_=-LedgerEntry.amount
            )
        ),
        0
    )


@router.get("/customers", response_model=list[CustomerResponse])
async def get_customers(
    current_shop: Shop = Depends(get_current_shop),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(
            Customer.id,
            Customer.name,
            balance_expression().label("balance")
        )
        .outerjoin(LedgerEntry, (Customer.id == LedgerEntry.customer_id) & (LedgerEntry.status == EntryStatus.confirmed))
        .where(Customer.shop_id == current_shop.id)
        .group_by(Customer.id, Customer.name)
        .order_by(Customer.name)
    )

    return [
        CustomerResponse(id=row.id, name=row.name, balance=float(row.balance))
        for row in result.all()
    ]


@router.get("/ledger/balance", response_model=BalanceResponse)
async def get_balance(
    customer_id: UUID,
    current_shop: Shop = Depends(get_current_shop),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(Customer).where(
            Customer.id == customer_id,
            Customer.shop_id == current_shop.id
        )
    )
    customer = result.scalar_one_or_none()

    if customer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Customer not found"
        )

    balance_result = await db.execute(
        select(balance_expression())
        .where(
            LedgerEntry.customer_id == customer_id,
            LedgerEntry.shop_id == current_shop.id,
            LedgerEntry.status == EntryStatus.confirmed
        )
    )
    balance = float(balance_result.scalar())

    return BalanceResponse(
        customer_id=customer.id,
        customer_name=customer.name,
        balance=balance
    )


@router.get("/ledger/summary", response_model=SummaryResponse)
async def get_summary(
    current_shop: Shop = Depends(get_current_shop),
    db: AsyncSession = Depends(get_db)
):
    total_result = await db.execute(
        select(balance_expression())
        .where(
            LedgerEntry.shop_id == current_shop.id,
            LedgerEntry.status == EntryStatus.confirmed
        )
    )
    total_outstanding = float(total_result.scalar())

    customer_count_result = await db.execute(
        select(func.count(Customer.id)).where(Customer.shop_id == current_shop.id)
    )
    customer_count = customer_count_result.scalar()

    return SummaryResponse(
        total_outstanding=total_outstanding,
        customer_count=customer_count
    )


@router.get("/ledger/entries", response_model=list[EntryResponse])
async def get_entries(
    customer_id: UUID,
    current_shop: Shop = Depends(get_current_shop),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(LedgerEntry)
        .where(
            LedgerEntry.customer_id == customer_id,
            LedgerEntry.shop_id == current_shop.id,
            LedgerEntry.status == EntryStatus.confirmed
        )
        .order_by(LedgerEntry.created_at.desc())
    )
    entries = result.scalars().all()
    return [
        EntryResponse(
            id=entry.id,
            amount=float(entry.amount),
            entry_type=entry.entry_type.value if hasattr(entry.entry_type, "value") else str(entry.entry_type),
            description=entry.description,
            created_at=entry.created_at.isoformat()
        )
        for entry in entries
    ]


@router.post("/customers/{customer_id}/clear", response_model=ClearKhataResponse)
async def clear_customer_khata(
    customer_id: UUID,
    payload: ClearKhataRequest,
    current_shop: Shop = Depends(get_current_shop),
    db: AsyncSession = Depends(get_db)
):
    """
    Soft-clears a customer's khata.
    CRITICAL: Requires explicit confirmation (payload.confirmed == True).
    Never permanently deletes; only sets status to cancelled.
    """
    if not payload.confirmed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Confirmation required before clearing khata"
        )

    result = await db.execute(
        select(Customer).where(
            Customer.id == customer_id,
            Customer.shop_id == current_shop.id
        )
    )
    customer = result.scalar_one_or_none()
    if customer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found")

    update_result = await db.execute(
        update(LedgerEntry)
        .where(
            LedgerEntry.customer_id == customer_id,
            LedgerEntry.shop_id == current_shop.id,
            LedgerEntry.status == EntryStatus.confirmed
        )
        .values(status=EntryStatus.cancelled)
    )
    row_count = update_result.rowcount or 0
    await db.flush()

    return ClearKhataResponse(
        status="confirmed",
        message=f"ٹھیک ہے، {customer.name} کا کھاتہ صاف کر دیا گیا ہے۔",
        cleared_entries_count=row_count
    )


@router.post("/entries/{entry_id}/cancel", response_model=ClearKhataResponse)
async def cancel_ledger_entry(
    entry_id: UUID,
    payload: ClearKhataRequest,
    current_shop: Shop = Depends(get_current_shop),
    db: AsyncSession = Depends(get_db)
):
    """
    Soft-cancels a single ledger entry.
    Requires explicit confirmation.
    """
    if not payload.confirmed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Confirmation required before cancelling entry"
        )

    result = await db.execute(
        select(LedgerEntry).where(
            LedgerEntry.id == entry_id,
            LedgerEntry.shop_id == current_shop.id,
            LedgerEntry.status == EntryStatus.confirmed
        )
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entry not found or already cancelled")

    entry.status = EntryStatus.cancelled
    await db.flush()

    return ClearKhataResponse(
        status="confirmed",
        message="اندراج کامیابی سے منسوخ کر دیا گیا ہے۔",
        cleared_entries_count=1
    )


@router.post("/ledger/entries", response_model=EntryResponse)
async def create_entry(
    payload: CreateEntryRequest,
    current_shop: Shop = Depends(get_current_shop),
    db: AsyncSession = Depends(get_db)
):
    """
    Creates an udhaar or wusool entry for a customer.
    Guardrail: If entry_type == 'wusool' and amount > 1000 and not payload.confirmed,
    raises 400 requiring explicit confirmation.
    """
    if payload.amount <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="رقم صفر سے زیادہ ہونی چاہیے۔"
        )

    # Validate entry_type
    raw_type = payload.entry_type.lower()
    if raw_type in ("wusool", "payment"):
        e_type = EntryType.payment
    elif raw_type == "udhaar":
        e_type = EntryType.udhaar
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="غلط اندراج قسم۔ صرف udhaar یا payment (یا wusool) ممکن ہے۔"
        )

    # Enforce payment guardrail: payment > 1000 requires explicit confirmation
    if e_type == EntryType.payment and payload.amount > 1000 and not payload.confirmed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="CONFIRMATION_REQUIRED_PAYMENT_OVER_1000"
        )

    # Verify customer belongs to current shop
    result = await db.execute(
        select(Customer).where(
            Customer.id == payload.customer_id,
            Customer.shop_id == current_shop.id
        )
    )
    customer = result.scalar_one_or_none()
    if customer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found")

    new_entry = LedgerEntry(
        shop_id=current_shop.id,
        customer_id=customer.id,
        amount=payload.amount,
        entry_type=e_type,
        status=EntryStatus.confirmed,
        description=payload.description
    )
    db.add(new_entry)
    await db.flush()

    return EntryResponse(
        id=new_entry.id,
        amount=float(new_entry.amount),
        entry_type=new_entry.entry_type.value if hasattr(new_entry.entry_type, "value") else str(new_entry.entry_type),
        description=new_entry.description,
        created_at=new_entry.created_at.isoformat()
    )



