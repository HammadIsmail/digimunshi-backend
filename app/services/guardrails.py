from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.models.models import Shop, Customer, LedgerEntry, EntryType, EntryStatus, PendingAction, ActionStatus
from datetime import datetime, timezone
import difflib
import json



def normalize_urdu_name(text: str) -> str:
    """Normalize common Urdu character variations (Yeh, Kaf, Heh, Alif)."""
    if not text:
        return ""
    text = text.strip()
    replacements = {
        'ك': 'ک',
        'ي': 'ی',
        'ى': 'ی',
        'ئ': 'ی',
        'ة': 'ہ',
        'ه': 'ہ',
        'ھ': 'ہ',
        'آ': 'ا',
        'أ': 'ا',
        'إ': 'ا',
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text.lower()


def _is_name_match(input_name: str, db_name: str) -> bool:
    """
    Careful name matching to prevent erroneous matches like 'حماد' -> 'احمد'.
    Character overlap or anagrams (ح-م-ا-د) must NEVER falsely match distinct people!
    """
    in_norm = normalize_urdu_name(input_name)
    db_norm = normalize_urdu_name(db_name)

    if not in_norm or not db_norm:
        return False

    # 1. Exact match
    if in_norm == db_norm:
        return True

    # 2. Word / Token match (e.g. "علی" in "علی قریشی" or "Ali" in "Ali Qureshi")
    in_words = in_norm.split()
    db_words = db_norm.split()
    if any(w in db_words for w in in_words) or any(w in in_words for w in db_words):
        return True

    # 3. For short names (<= 4 chars like حماد, احمد, علی, عمر), distinct names must not fuzzy match!
    if len(in_norm) <= 4 or len(db_norm) <= 4:
        return False

    # 4. Must start with the same letter
    if in_norm[0] != db_norm[0]:
        return False

    # 5. Sequence matcher with high threshold (>= 0.88)
    return difflib.SequenceMatcher(None, in_norm, db_norm).ratio() >= 0.88


async def find_matching_customers(db: AsyncSession, shop_id, name: str) -> list:
    """Accurately match customer name within a shop's ledger."""
    result = await db.execute(
        select(Customer)
        .where(Customer.shop_id == shop_id)
    )
    customers = result.scalars().all()

    if not name:
        return []

    name_clean = name.strip()
    matches = []
    for c in customers:
        if _is_name_match(name_clean, c.name):
            matches.append(c)

    return matches



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
