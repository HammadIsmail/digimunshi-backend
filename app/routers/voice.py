import os
import json
import base64
import logging
from typing import Optional
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, status, Request
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, case as sa_case, update
from uuid import UUID

from app.core.database import get_db
from app.core.deps import get_current_shop
from app.models.models import Shop, Customer, LedgerEntry, EntryType, EntryStatus, PendingAction, ActionStatus
from app.schemas.schemas import VoiceProcessResponse, VoiceConfirmRequest, VoiceConfirmResponse
from app.services.voice import transcribe_audio, synthesize_speech, create_realtime_session, AUDIO_DIR
from app.services.intent import classify_intent
from app.services.guardrails import (
    find_matching_customers,
    check_unusual_amount,
    check_low_confidence,
    create_pending_action,
    resolve_pending_action,
)

router = APIRouter(tags=["voice"])
logger = logging.getLogger(__name__)

RESPONSES = {
    "confirm_add_existing": "{name} کا پہلے سے ادھار {balance} روپے تھا۔ کیا اس میں {amount} روپے اور ادھار لکھ دوں؟",
    "saved_new_customer": "ٹھیک ہے، {name} کا نیا کھاتہ بنا کے {amount} روپے لکھ دیے۔",
    "saved_existing": "ٹھیک ہے، {name} کے کھاتے میں {amount} روپے ادھار لکھ دیے۔",
    "saved_payment": "ٹھیک ہے، {name} کے کھاتے میں سے {amount} روپے کم کر دیے ہیں۔ اب باقی ادھار {balance} روپے ہے۔",
    "saved": "ٹھیک ہے، {name} کا کھاتہ اپ ڈیٹ کر دیا گیا ہے۔",
    "query_single": "{name} کا {amount} روپے ادھار باقی ہے۔",
    "query_all": "سب ملا کر {total} روپے ادھار باقی ہے، {count} گاہکوں کا۔",
    "disambiguate": "دو {name} ہیں — {options} میں سے کون سے؟",
    "delete_confirm": "{name} کا پورا کھاتہ صاف کرنا ہے، {amount} روپے — پکا؟",
    "deleted": "ٹھیک ہے، {name} کا کھاتہ صاف کر دیا گیا ہے۔",
    "unusual_amount": "{amount} روپے؟ کیا میں نے صحیح سنا؟",
    "low_confidence": "ٹھیک سے سن نہیں سکا، دوبارہ بولیے؟",
    "unknown_intent": "ٹھیک سے سن نہیں سکا، دوبارہ بولیے؟",
    "cancel": "ٹھیک ہے، کینسل کر دیا گیا ہے۔",
}


def get_response_text(key: str, **kwargs) -> str:
    return RESPONSES.get(key, "سمجھ نہیں آیا، دوبارہ بولیے۔").format(**kwargs)


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


@router.post("/voice/process", response_model=VoiceProcessResponse)
async def process_voice(
    request: Request,
    current_shop: Shop = Depends(get_current_shop),
    db: AsyncSession = Depends(get_db),
):
    content_type_header = request.headers.get("content-type", "")

    if "application/json" in content_type_header:
        body = await request.json()
        audio_b64 = body.get("audio_base64", "")
        session_id = body.get("session_id", "default")
        audio_bytes = base64.b64decode(audio_b64)
        fmt = body.get("format", "wav").lower()
        filename = f"recording.{fmt}"
        content_type = "audio/wav" if fmt == "wav" else "audio/mp4"
    else:
        form = await request.form()
        audio = form.get("audio")
        session_id = form.get("session_id", "default")
        if hasattr(audio, "read"):
            audio_bytes = await audio.read()
            filename = getattr(audio, "filename", "recording.wav")
            content_type = getattr(audio, "content_type", "audio/wav")
        else:
            audio_bytes = b""
            filename = "recording.wav"
            content_type = "audio/wav"

    logger.info(f"Processing voice upload: {len(audio_bytes)} bytes, filename: {filename}, content_type: {content_type}")

    try:
        stt_result = await transcribe_audio(audio_bytes, filename, content_type)
        safe_transcript_preview = stt_result.get("text", "").encode("ascii", "backslashreplace").decode("ascii")
        logger.info(f"STT result: len={len(stt_result.get('text', ''))}, conf={stt_result.get('confidence')}, preview='{safe_transcript_preview[:60]}'")
    except Exception as e:
        logger.error(f"STT process error: {e}")
        response_text = get_response_text("low_confidence")
        audio_url = await synthesize_speech(response_text)
        return VoiceProcessResponse(
            transcript="", intent="unknown", requires_confirmation=False,
            pending_action_id=None, response_text=response_text,
            response_audio_url=audio_url, resolved_entities={"error": str(e)}
        )

    transcript = stt_result["text"]
    confidence = stt_result["confidence"]

    if not transcript or await check_low_confidence(confidence, current_shop):
        response_text = get_response_text("low_confidence")
        audio_url = await synthesize_speech(response_text)
        return VoiceProcessResponse(
            transcript=transcript, intent="unknown", requires_confirmation=False,
            pending_action_id=None, response_text=response_text,
            response_audio_url=audio_url, resolved_entities={"confidence": confidence}
        )

    # Check if there is an active pending action awaiting confirmation for this session
    pa_result = await db.execute(
        select(PendingAction).where(
            PendingAction.shop_id == current_shop.id,
            PendingAction.session_id == session_id,
            PendingAction.status == ActionStatus.pending
        ).order_by(PendingAction.created_at.desc())
    )
    pending_action = pa_result.scalars().first()

    if pending_action:
        t_clean = transcript.strip().lower()
        is_yes = any(w in t_clean for w in [
            "ہاں", "جی", "ٹھیک ہے", "لکھ دو", "کردو", "کر دو", "منظور", "صحیح", "پکا",
            "haan", "han", "ji", "theek", "theek hai", "likh do", "kar do", "yes", "ok", "confirm", "sahi", "pakka"
        ])
        is_no = any(w in t_clean for w in [
            "نہیں", "کینسل", "مت", "رہنے دو", "نا", "غلط",
            "nahi", "nahin", "cancel", "mat", "rehne do", "na", "no", "galat"
        ])

        if is_yes or is_no:
            action = await resolve_pending_action(
                db, pending_action.id, current_shop.id, is_yes
            )
            response_text, audio_url, entry_id = await apply_pending_action(
                db, current_shop, action, is_yes
            )
            return VoiceProcessResponse(
                transcript=transcript,
                intent="confirm" if is_yes else "cancel",
                requires_confirmation=False,
                pending_action_id=None,
                response_text=response_text,
                response_audio_url=audio_url,
                resolved_entities={"confirmed": is_yes, "ledger_entry_id": str(entry_id) if entry_id else None}
            )

    try:
        intent_result = await classify_intent(transcript)
    except Exception:
        intent_result = {"intent": "unknown", "customer_name": None, "amount": None, "confidence": 0.0}

    intent = intent_result["intent"]
    customer_name = intent_result.get("customer_name")
    amount = intent_result.get("amount")

    if pending_action and intent in ("confirm", "cancel"):
        confirmed = (intent == "confirm")
        action = await resolve_pending_action(
            db, pending_action.id, current_shop.id, confirmed
        )
        response_text, audio_url, entry_id = await apply_pending_action(
            db, current_shop, action, confirmed
        )
        return VoiceProcessResponse(
            transcript=transcript,
            intent=intent,
            requires_confirmation=False,
            pending_action_id=None,
            response_text=response_text,
            response_audio_url=audio_url,
            resolved_entities={"confirmed": confirmed, "ledger_entry_id": str(entry_id) if entry_id else None}
        )

    if intent == "unknown":
        response_text = get_response_text("unknown_intent")
        audio_url = await synthesize_speech(response_text)
        return VoiceProcessResponse(
            transcript=transcript, intent=intent, requires_confirmation=False,
            pending_action_id=None, response_text=response_text,
            response_audio_url=audio_url, resolved_entities={}
        )

    if intent == "add_customer" and customer_name:
        matches = await find_matching_customers(db, current_shop.id, customer_name)
        if len(matches) > 0:
            response_text = f"{matches[0].name} pehle se khate mein mojood hain."
            audio_url = await synthesize_speech(response_text)
            return VoiceProcessResponse(
                transcript=transcript, intent=intent, requires_confirmation=False,
                pending_action_id=None, response_text=response_text,
                response_audio_url=audio_url,
                resolved_entities={"customer_id": str(matches[0].id), "customer_name": matches[0].name}
            )
        else:
            new_customer = Customer(shop_id=current_shop.id, name=customer_name)
            db.add(new_customer)
            await db.flush()
            response_text = f"ٹھیک ہے، {customer_name} کا نیا کھاتہ کھول دیا گیا ہے۔"
            audio_url = await synthesize_speech(response_text)
            return VoiceProcessResponse(
                transcript=transcript, intent=intent, requires_confirmation=False,
                pending_action_id=None, response_text=response_text,
                response_audio_url=audio_url,
                resolved_entities={"customer_id": str(new_customer.id), "customer_name": new_customer.name}
            )

    if intent == "query_balance_all":
        result = await db.execute(
            select(balance_expression())
            .where(LedgerEntry.shop_id == current_shop.id, LedgerEntry.status == EntryStatus.confirmed)
        )
        total = float(result.scalar())
        count_result = await db.execute(
            select(func.count(Customer.id)).where(Customer.shop_id == current_shop.id)
        )
        count = count_result.scalar()
        response_text = get_response_text("query_all", total=int(total), count=count)
        audio_url = await synthesize_speech(response_text)
        return VoiceProcessResponse(
            transcript=transcript, intent=intent, requires_confirmation=False,
            pending_action_id=None, response_text=response_text,
            response_audio_url=audio_url,
            resolved_entities={"total_outstanding": total, "customer_count": count}
        )

    if intent == "query_balance_single" and customer_name:
        matches = await find_matching_customers(db, current_shop.id, customer_name)
        if len(matches) == 0:
            response_text = f"{customer_name} نام کا کوئی گاہک کھاتے میں موجود نہیں ہے۔"
            audio_url = await synthesize_speech(response_text)
            return VoiceProcessResponse(
                transcript=transcript, intent=intent, requires_confirmation=False,
                pending_action_id=None, response_text=response_text,
                response_audio_url=audio_url,
                resolved_entities={"customer_name": customer_name}
            )
        if len(matches) > 1:
            options = " یا ".join([m.name for m in matches[:3]])
            response_text = get_response_text("disambiguate", name=customer_name, options=options)
            audio_url = await synthesize_speech(response_text)
            return VoiceProcessResponse(
                transcript=transcript, intent=intent, requires_confirmation=False,
                pending_action_id=None, response_text=response_text,
                response_audio_url=audio_url,
                resolved_entities={"ambiguous_matches": [m.name for m in matches]}
            )
        customer = matches[0]
        bal_result = await db.execute(
            select(balance_expression())
            .where(
                LedgerEntry.customer_id == customer.id,
                LedgerEntry.shop_id == current_shop.id,
                LedgerEntry.status == EntryStatus.confirmed
            )
        )
        balance = float(bal_result.scalar())
        response_text = get_response_text("query_single", name=customer.name, amount=int(balance))
        audio_url = await synthesize_speech(response_text)
        return VoiceProcessResponse(
            transcript=transcript, intent=intent, requires_confirmation=False,
            pending_action_id=None, response_text=response_text,
            response_audio_url=audio_url,
            resolved_entities={"customer_id": str(customer.id), "customer_name": customer.name, "balance": balance}
        )

    if intent in ("add_entry", "record_payment", "delete_entry") and customer_name:
        matches = await find_matching_customers(db, current_shop.id, customer_name)

        if len(matches) == 0:
            if intent == "add_entry" and amount:
                # Stage 3: First Voice Entry — New Customer (Direct Create with ZERO friction)
                new_customer = Customer(
                    shop_id=current_shop.id,
                    name=customer_name
                )
                db.add(new_customer)
                await db.flush()

                entry = LedgerEntry(
                    shop_id=current_shop.id,
                    customer_id=new_customer.id,
                    amount=amount,
                    entry_type=EntryType.udhaar,
                    status=EntryStatus.confirmed,
                )
                db.add(entry)
                await db.flush()

                response_text = get_response_text("saved_new_customer", name=new_customer.name, amount=int(amount))
                audio_url = await synthesize_speech(response_text)
                return VoiceProcessResponse(
                    transcript=transcript, intent=intent, requires_confirmation=False,
                    pending_action_id=None, response_text=response_text,
                    response_audio_url=audio_url,
                    ledger_updated=True,
                    resolved_entities={
                        "customer_id": str(new_customer.id),
                        "customer_name": new_customer.name,
                        "amount": amount,
                        "is_new_customer": True,
                        "ledger_entry_id": str(entry.id)
                    }
                )


            response_text = f"{customer_name} نام کا کوئی گاہک کھاتے میں موجود نہیں ہے۔"
            audio_url = await synthesize_speech(response_text)
            return VoiceProcessResponse(
                transcript=transcript, intent=intent, requires_confirmation=False,
                pending_action_id=None, response_text=response_text,
                response_audio_url=audio_url,
                resolved_entities={"customer_name": customer_name}
            )

        if len(matches) > 1:
            # Stage 6: Disambiguation Guardrail
            options = " یا ".join([m.name for m in matches[:3]])
            response_text = get_response_text("disambiguate", name=customer_name, options=options)
            audio_url = await synthesize_speech(response_text)
            return VoiceProcessResponse(
                transcript=transcript, intent=intent, requires_confirmation=False,
                pending_action_id=None, response_text=response_text,
                response_audio_url=audio_url,
                resolved_entities={"ambiguous_matches": [m.name for m in matches]}
            )

        customer = matches[0]

        # Calculate current customer balance for balance readback
        bal_result = await db.execute(
            select(balance_expression())
            .where(
                LedgerEntry.customer_id == customer.id,
                LedgerEntry.shop_id == current_shop.id,
                LedgerEntry.status == EntryStatus.confirmed
            )
        )
        current_balance = float(bal_result.scalar() or 0.0)

        # Handling payment / debt reduction (kam kar do / vasool ho gaye)
        if intent == "record_payment" and amount:
            entry = LedgerEntry(
                shop_id=current_shop.id,
                customer_id=customer.id,
                amount=amount,
                entry_type=EntryType.payment,
                status=EntryStatus.confirmed,
            )
            db.add(entry)
            await db.flush()

            new_balance = max(0, int(current_balance - amount))
            response_text = get_response_text("saved_payment", name=customer.name, amount=int(amount), balance=new_balance)
            audio_url = await synthesize_speech(response_text)
            return VoiceProcessResponse(
                transcript=transcript, intent=intent, requires_confirmation=False,
                pending_action_id=None, response_text=response_text,
                response_audio_url=audio_url,
                ledger_updated=True,
                resolved_entities={
                    "customer_id": str(customer.id),
                    "customer_name": customer.name,
                    "amount": amount,
                    "previous_balance": current_balance,
                    "new_balance": new_balance,
                    "entry_type": "payment"
                }
            )


        if intent == "delete_entry":
            # Stage 9: Clearing a Khata — read back current balance and require explicit yes
            reason = get_response_text("delete_confirm", name=customer.name, amount=int(current_balance))
            action = await create_pending_action(
                db, current_shop.id, session_id, intent,
                {"customer_id": str(customer.id), "customer_name": customer.name, "amount": current_balance}, reason
            )
            audio_url = await synthesize_speech(reason)
            return VoiceProcessResponse(
                transcript=transcript, intent=intent, requires_confirmation=True,
                pending_action_id=action.id, response_text=reason,
                response_audio_url=audio_url,
                resolved_entities={"customer_id": str(customer.id), "customer_name": customer.name, "balance": current_balance}
            )

        if intent == "add_entry" and amount:
            # Stage 7: Unusual Amount Guardrail
            if await check_unusual_amount(current_shop, db, amount):
                confirm_text = get_response_text("unusual_amount", amount=int(amount))
            else:
                # Stage 4: Return Customer — Existing-Customer Confirmation with previous balance readback!
                confirm_text = get_response_text("confirm_add_existing", name=customer.name, balance=int(current_balance), amount=int(amount))

            action = await create_pending_action(
                db, current_shop.id, session_id, intent,
                {
                    "customer_id": str(customer.id),
                    "customer_name": customer.name,
                    "amount": amount,
                    "previous_balance": current_balance
                },
                confirm_text
            )
            audio_url = await synthesize_speech(confirm_text)
            return VoiceProcessResponse(
                transcript=transcript, intent=intent, requires_confirmation=True,
                pending_action_id=action.id, response_text=confirm_text,
                response_audio_url=audio_url,
                resolved_entities={
                    "customer_id": str(customer.id),
                    "customer_name": customer.name,
                    "amount": amount,
                    "previous_balance": current_balance
                }
            )

    response_text = get_response_text("unknown_intent")
    audio_url = await synthesize_speech(response_text)
    return VoiceProcessResponse(
        transcript=transcript, intent=intent, requires_confirmation=False,
        pending_action_id=None, response_text=response_text,
        response_audio_url=audio_url, resolved_entities={}
    )


async def apply_pending_action(
    db: AsyncSession,
    current_shop: Shop,
    action: PendingAction,
    confirmed: bool,
) -> tuple[str, str | None, Optional[UUID]]:
    """Applies or cancels the pending action in the database and returns (response_text, audio_url, ledger_entry_id)."""
    if not confirmed:
        response_text = get_response_text("cancel")
        audio_url = await synthesize_speech(response_text)
        return response_text, audio_url, None

    payload = json.loads(action.payload)

    if action.intent == "create_customer_and_add_entry":
        existing = await find_matching_customers(db, current_shop.id, payload["customer_name"])
        if existing:
            customer = existing[0]
        else:
            customer = Customer(
                shop_id=current_shop.id,
                name=payload["customer_name"]
            )
            db.add(customer)
            await db.flush()

        entry = LedgerEntry(
            shop_id=current_shop.id,
            customer_id=customer.id,
            amount=payload["amount"],
            entry_type=EntryType.udhaar,
            status=EntryStatus.confirmed,
        )
        db.add(entry)
        await db.flush()

        response_text = get_response_text("saved_new_customer", name=customer.name, amount=int(payload["amount"]))
        audio_url = await synthesize_speech(response_text)
        return response_text, audio_url, entry.id

    if action.intent == "add_entry":
        entry = LedgerEntry(
            shop_id=current_shop.id,
            customer_id=UUID(payload["customer_id"]),
            amount=payload["amount"],
            entry_type=EntryType.udhaar,
            status=EntryStatus.confirmed,
        )
        db.add(entry)
        await db.flush()
        response_text = get_response_text("saved_existing", name=payload["customer_name"], amount=int(payload["amount"]))
        audio_url = await synthesize_speech(response_text)
        return response_text, audio_url, entry.id

    if action.intent == "record_payment":
        entry = LedgerEntry(
            shop_id=current_shop.id,
            customer_id=UUID(payload["customer_id"]),
            amount=payload["amount"],
            entry_type=EntryType.payment,
            status=EntryStatus.confirmed,
        )
        db.add(entry)
        await db.flush()
        prev_bal = payload.get("previous_balance", 0.0)
        new_bal = max(0, int(prev_bal - payload["amount"]))
        response_text = get_response_text("saved_payment", name=payload["customer_name"], amount=int(payload["amount"]), balance=new_bal)
        audio_url = await synthesize_speech(response_text)
        return response_text, audio_url, entry.id

    if action.intent == "delete_entry":
        await db.execute(
            update(LedgerEntry)
            .where(
                LedgerEntry.customer_id == UUID(payload["customer_id"]),
                LedgerEntry.shop_id == current_shop.id,
                LedgerEntry.status == EntryStatus.confirmed
            )
            .values(status=EntryStatus.cancelled)
        )
        response_text = get_response_text("deleted", name=payload["customer_name"])
        audio_url = await synthesize_speech(response_text)
        return response_text, audio_url, None

    return "ہو گیا۔", None, None


@router.post("/voice/confirm", response_model=VoiceConfirmResponse)
async def confirm_voice(
    request: VoiceConfirmRequest,
    current_shop: Shop = Depends(get_current_shop),
    db: AsyncSession = Depends(get_db),
):
    action = await resolve_pending_action(
        db, request.pending_action_id, current_shop.id, request.confirmed
    )

    if action is None:
        raise HTTPException(status_code=404, detail="Pending action not found")

    response_text, audio_url, entry_id = await apply_pending_action(
        db, current_shop, action, request.confirmed
    )

    return VoiceConfirmResponse(
        status="confirmed" if request.confirmed else "cancelled",
        response_text=response_text,
        response_audio_url=audio_url,
        ledger_entry_id=entry_id,
        ledger_updated=request.confirmed,
    )



@router.get("/voice/audio/{audio_id}.mp3")
async def get_voice_audio(audio_id: str):
    """Serve synthesized MP3 audio files generated by Uplift Orator."""
    audio_path = os.path.join(AUDIO_DIR, f"{audio_id}.mp3")
    if not os.path.isfile(audio_path):
        raise HTTPException(status_code=404, detail="Audio file not found")
    return FileResponse(audio_path, media_type="audio/mpeg")


@router.post("/voice/realtime-session")
async def get_realtime_session(
    current_shop: Shop = Depends(get_current_shop),
):
    """
    Create a LiveKit WebRTC session with Uplift AI Realtime Assistant.
    Docs: https://docs.upliftai.org/assistants/introduction
    """
    try:
        session = await create_realtime_session(participant_name=current_shop.owner_name or "Shopkeeper")
        return session
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to create Uplift realtime session: {str(e)}")
