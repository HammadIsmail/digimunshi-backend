from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, case as sa_case
from uuid import UUID

from app.core.database import get_db
from app.core.deps import get_current_shop
from app.models.models import Shop, Customer, LedgerEntry, EntryType, EntryStatus
from app.schemas.schemas import CustomerResponse, BalanceResponse, SummaryResponse, EntryResponse

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
            created_at=entry.created_at.isoformat()
        )
        for entry in entries
    ]

