from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.models.models import Shop, Customer, LedgerEntry, EntryType, EntryStatus, PendingAction, ActionStatus
from datetime import datetime, timezone
import json


async def find_matching_customers(db: AsyncSession, shop_id, name: str) -> list:
    """Fuzzy match customer name within a shop."""
    result = await db.execute(
        select(Customer)
        .where(Customer.shop_id == shop_id)
    )
    customers = result.scalars().all()

    if not name:
        return []

    name_lower = name.lower().strip()
    matches = []
    for c in customers:
        c_name_lower = c.name.lower()
        if name_lower in c_name_lower or c_name_lower in name_lower:
            matches.append(c)
        elif _fuzzy_match(name_lower, c_name_lower):
            matches.append(c)

    return matches


def _fuzzy_match(input_name: str, db_name: str) -> bool:
    """Simple fuzzy matching using character overlap."""
    if not input_name or not db_name:
        return False
    input_set = set(input_name)
    db_set = set(db_name)
    overlap = len(input_set & db_set)
    max_len = max(len(input_set), len(db_set))
    return overlap / max_len > 0.7 if max_len > 0 else False


async def check_unusual_amount(shop: Shop, db: AsyncSession, amount: float) -> bool:
    """Check if amount is outside the shop's normal range."""
    if amount > float(shop.unusual_amount_threshold):
        return True

    result = await db.execute(
        select(func.avg(LedgerEntry.amount))
        .where(
            LedgerEntry.shop_id == shop.id,
            LedgerEntry.status == EntryStatus.confirmed
        )
    )
    avg = result.scalar()
    if avg and amount > float(avg) * 5:
        return True

    return False


async def check_low_confidence(confidence: float, shop: Shop) -> bool:
    """Check if STT confidence is below threshold."""
    return confidence < float(shop.stt_confidence_threshold)


async def create_pending_action(
    db: AsyncSession,
    shop_id,
    session_id: str,
    intent: str,
    payload: dict,
    reason: str
) -> PendingAction:
    """Create a pending action for confirmation."""
    action = PendingAction(
        shop_id=shop_id,
        session_id=session_id,
        intent=intent,
        payload=json.dumps(payload),
        reason=reason,
        status=ActionStatus.pending
    )
    db.add(action)
    await db.flush()
    return action


async def resolve_pending_action(
    db: AsyncSession,
    pending_action_id,
    shop_id,
    confirmed: bool
) -> PendingAction:
    """Resolve a pending action. Returns the action or None if not found/not owned."""
    result = await db.execute(
        select(PendingAction).where(
            PendingAction.id == pending_action_id,
            PendingAction.shop_id == shop_id,
            PendingAction.status == ActionStatus.pending
        )
    )
    action = result.scalar_one_or_none()

    if action is None:
        return None

    action.status = ActionStatus.confirmed if confirmed else ActionStatus.cancelled
    action.resolved_at = datetime.now(timezone.utc)
    await db.flush()

    return action
