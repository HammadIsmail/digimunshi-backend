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
    "confirm_add_existing_item": "{name} کا پہلے سے ادھار {balance} روپے تھا۔ کیا اس میں {item} کے {amount} روپے اور ادھار لکھ دوں؟",
    "saved_new_customer": "ٹھیک ہے، {name} کا نیا کھاتہ بنا کے {amount} روپے لکھ دیے۔",
    "saved_new_customer_item": "ٹھیک ہے، {name} کا نیا کھاتہ بنا کے {item} کے {amount} روپے لکھ دیے۔",
    "saved_existing": "ٹھیک ہے، {name} کے کھاتے میں {amount} روپے ادھار لکھ دیے۔",
    "saved_existing_item": "ٹھیک ہے، {name} کے کھاتے میں {item} کے {amount} روپے ادھار لکھ دیے۔",

    "saved_payment": "ٹھیک ہے، {name} کے کھاتے میں سے {amount} روپے کم کر دیے ہیں۔ اب باقی ادھار {balance} روپے ہے۔",
    "confirm_payment": "کیا {name} کے کھاتے میں سے {amount} روپے وصولی درج کر دوں؟",
    "saved": "ٹھیک ہے، {name} کا کھاتہ اپ ڈیٹ کر دیا گیا ہے۔",
    "query_single": "{name} کا {amount} روپے ادھار باقی ہے۔",
    "query_all": "سب ملا کر {total} روپے ادھار باقی ہے، {count} گاہکوں کا۔",
    "disambiguate": "دو {name} ہیں — {options} میں سے کون سے؟",
    "delete_confirm": "{name} کا پورا کھاتہ صاف کرنا ہے، {amount} روپے — پکا؟",
    "delete_no_name": "کس کا کھاتہ صاف کرنا ہے؟ گاہک کا نام بتائیے۔",
    "deleted": "ٹھیک ہے، {name} کا کھاتہ صاف کر دیا گیا ہے۔",
    "unusual_amount": "{amount} روپے؟ کیا میں نے صحیح سنا؟",
    "low_confidence": "ٹھیک سے سن نہیں سکا، دوبارہ بولیے؟",
    "unknown_intent": "ٹھیک سے سن نہیں سکا، دوبارہ بولیے؟",
    "cancel": "ٹھیک ہے، کینسل کر دیا گیا ہے۔",
}


COMMON_URDU_TRANSLITERATION = {
    "ali": "علی",
    "hammad": "حماد",
    "hamad": "حماد",
    "ahmed": "احمد",
    "ahmad": "احمد",
    "aslam": "اسلم",
    "kamran": "کامران",
    "bilal": "بلال",
    "usman": "عثمان",
    "osman": "عثمان",
    "zubair": "زبیر",
    "tariq": "طارق",
    "waqas": "وقاص",
    "rashid": "راشد",
    "imran": "عمران",
    "babar": "بابر",
    "rizwan": "رضوان",
    "faisal": "فیصل",
    "farhan": "فرحان",
    "kashif": "کاشف",
    "irfan": "عرفان",
    "sajid": "ساجد",
    "shahid": "شاہد",
    "zahid": "زاہد",
    "naveed": "نوید",
    "waseem": "وسیم",
    "wasim": "وسیم",
    "shoaib": "شعیب",
    "akram": "اکرم",
    "javed": "جاوید",
    "noman": "نعمان",
    "salman": "سلمان",
    "adnan": "عدنان",
    "hamza": "حمزہ",
    "umer": "عمر",
    "umar": "عمر",
    "omar": "عمر",
    "hassan": "حسن",
    "hasan": "حسن",
    "hussain": "حسین",
    "mohammad": "محمد",
    "muhammad": "محمد",
    "khan": "خان",
    "malik": "ملک",
    "chaudhry": "چوہدری",
    "chaudhary": "چوہدری",
    "bhai": "بھائی",
    "sahab": "صاحب",
    "bed": "بیڈ",
    "cheeni": "چینی",
    "chini": "چینی",
    "doodh": "دودھ",
    "aata": "آٹا",
    "ghee": "گھی",
    "tel": "تیل",
    "chawal": "چاول",
    "sabun": "صابن",
    "chai": "چائے",
}

def to_urdu_script(text: Optional[str]) -> str:
    if not text:
        return ""
    text_clean = text.strip()
    # Check if string already contains mostly Arabic/Urdu unicode chars
    urdu_chars = sum(1 for c in text_clean if '\u0600' <= c <= '\u06FF')
    if urdu_chars > 0 and urdu_chars >= len(text_clean) / 2:
        return text_clean

    # Convert Latin/Roman Urdu words into pure Urdu script
    words = text_clean.split()
    converted_words = []
    for w in words:
        w_lower = w.lower().strip(".,!?:;'\"")
        converted_words.append(COMMON_URDU_TRANSLITERATION.get(w_lower, w))
    return " ".join(converted_words)


def get_response_text(key: str, **kwargs) -> str:
    cleaned = {}
    for k, v in kwargs.items():
        if k in ("name", "item", "options") and isinstance(v, str):
            cleaned[k] = to_urdu_script(v)
        else:
            cleaned[k] = v
    return RESPONSES.get(key, "سمجھ نہیں آیا، دوبارہ بولیے۔").format(**cleaned)


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
    item = intent_result.get("item")

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
            ledger_updated=confirmed,
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

    if intent == "list_debtors":
        result = await db.execute(
            select(
                Customer.name,
                balance_expression().label("balance")
            )
            .join(LedgerEntry, (Customer.id == LedgerEntry.customer_id) & (LedgerEntry.status == EntryStatus.confirmed))
            .where(Customer.shop_id == current_shop.id)
            .group_by(Customer.id, Customer.name)
            .having(balance_expression() > 0)
            .order_by(balance_expression().desc())
        )
        debtors = result.all()
        if not debtors:
            response_text = "اس وقت کسی بھی گاہک کا کوئی ادھار باقی نہیں ہے۔"
        else:
            debtor_phrases = [f"{to_urdu_script(d.name)} کے {int(d.balance)} روپے" for d in debtors[:5]]
            if len(debtor_phrases) == 1:
                response_text = f"صرف {debtor_phrases[0]} ادھار باقی ہے۔"
            else:
                response_text = "، ".join(debtor_phrases[:-1]) + " اور " + debtor_phrases[-1] + " ادھار باقی ہیں۔"
            if len(debtors) > 5:
                response_text += f" اس کے علاوہ مزید {len(debtors) - 5} گاہکوں کے بقایا جات ہیں۔"

        audio_url = await synthesize_speech(response_text)
        return VoiceProcessResponse(
            transcript=transcript, intent=intent, requires_confirmation=False,
            pending_action_id=None, response_text=response_text,
            response_audio_url=audio_url,
            resolved_entities={"debtors_count": len(debtors)}
        )


    if intent == "add_customer" and customer_name:
        matches = await find_matching_customers(db, current_shop.id, customer_name)
        if len(matches) > 0:
            response_text = f"{to_urdu_script(matches[0].name)} پہلے سے کھاتے میں موجود ہیں۔"
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
            response_text = f"ٹھیک ہے، {to_urdu_script(customer_name)} کا نیا کھاتہ کھول دیا گیا ہے۔"
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
            response_text = f"{to_urdu_script(customer_name)} نام کا کوئی گاہک کھاتے میں موجود نہیں ہے۔"
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

    if intent == "delete_entry" and not customer_name:
        response_text = get_response_text("delete_no_name")
        audio_url = await synthesize_speech(response_text)
        return VoiceProcessResponse(
            transcript=transcript, intent=intent, requires_confirmation=False,
            pending_action_id=None, response_text=response_text,
            response_audio_url=audio_url, resolved_entities={}
        )

    if intent in ("add_entry", "record_payment", "delete_entry") and customer_name:
        matches = await find_matching_customers(db, current_shop.id, customer_name)

        if len(matches) == 0:
            if intent == "add_entry" and amount:
                # Unusual Amount Guardrail Check (Enforce on new customer as well!)
                if await check_unusual_amount(current_shop, db, amount):
                    reason = get_response_text("unusual_amount", amount=int(amount))
                    action = await create_pending_action(
                        db, current_shop.id, session_id, "create_customer_and_add_entry",
                        {"customer_name": customer_name, "amount": amount, "item": item}, reason
                    )
                    audio_url = await synthesize_speech(reason)
                    return VoiceProcessResponse(
                        transcript=transcript, intent=intent, requires_confirmation=True,
                        pending_action_id=action.id, response_text=reason,
                        response_audio_url=audio_url,
                        resolved_entities={"customer_name": customer_name, "amount": amount, "item": item, "is_new_customer": True}
                    )

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
                    description=item,
                    entry_type=EntryType.udhaar,
                    status=EntryStatus.confirmed,
                )
                db.add(entry)
                await db.flush()

                if item:
                    response_text = get_response_text("saved_new_customer_item", name=new_customer.name, amount=int(amount), item=item)
                else:
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
                        "item": item,
                        "is_new_customer": True,
                        "ledger_entry_id": str(entry.id)
                    }
                )



            response_text = f"{to_urdu_script(customer_name)} نام کا کوئی گاہک کھاتے میں موجود نہیں ہے۔"
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
            # Payment confirmation required for amounts above 1 thousand
            if amount > 1000:
                confirm_text = get_response_text("confirm_payment", name=customer.name, amount=int(amount))
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
                        "previous_balance": current_balance,
                        "entry_type": "payment"
                    }
                )

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
                if item:
                    confirm_text = get_response_text("confirm_add_existing_item", name=customer.name, balance=int(current_balance), amount=int(amount), item=item)
                else:
                    confirm_text = get_response_text("confirm_add_existing", name=customer.name, balance=int(current_balance), amount=int(amount))

            action = await create_pending_action(
                db, current_shop.id, session_id, intent,
                {
                    "customer_id": str(customer.id),
                    "customer_name": customer.name,
                    "amount": amount,
                    "item": item,
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
                    "item": item,
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

        item = payload.get("item")
        entry = LedgerEntry(
            shop_id=current_shop.id,
            customer_id=customer.id,
            amount=payload["amount"],
            description=item,
            entry_type=EntryType.udhaar,
            status=EntryStatus.confirmed,
        )
        db.add(entry)
        await db.flush()

        if item:
            response_text = get_response_text("saved_new_customer_item", name=customer.name, amount=int(payload["amount"]), item=item)
        else:
            response_text = get_response_text("saved_new_customer", name=customer.name, amount=int(payload["amount"]))
        audio_url = await synthesize_speech(response_text)
        return response_text, audio_url, entry.id

    if action.intent == "add_entry":
        item = payload.get("item")
        entry = LedgerEntry(
            shop_id=current_shop.id,
            customer_id=UUID(payload["customer_id"]),
            amount=payload["amount"],
            description=item,
            entry_type=EntryType.udhaar,
            status=EntryStatus.confirmed,
        )
        db.add(entry)
        await db.flush()
        if item:
            response_text = get_response_text("saved_existing_item", name=payload["customer_name"], amount=int(payload["amount"]), item=item)
        else:
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
