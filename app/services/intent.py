import re
import json
import logging
import httpx
from groq import AsyncGroq
from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

GROQ_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "record_udhaar",
            "description": "Record a credit (udhaar) entry for a customer. Used when customer takes items on credit or user wants to add/write udhaar (e.g. 'Ali ko 500 rupay udhaar likho', 'Hammad ne 20,000 ka bed udhaar liya hai', 'Kamran ke khate mein cheeni ke 400 likh do').",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_name": {"type": "string", "description": "Customer name strictly in authentic Urdu script (نستعلیق / اردو رسم الخط e.g. علی, حماد, اسلم, کامران)"},
                    "amount": {"type": "number", "description": "Amount in Pakistani Rupees"},
                    "item": {"type": "string", "description": "Item, goods, or reason for udhaar strictly in authentic Urdu script (e.g. بیڈ, راشن, چینی, سیمنٹ, دودھ, پنکھا). Extract if mentioned, otherwise omit."}
                },
                "required": ["customer_name", "amount"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_debtors",
            "description": "List the names of all customers who currently have outstanding udhaar along with their individual balances. Used when user asks 'Gahkon ke naam batao jin ka udhaar rehta hai', 'Kin kin ka udhaar baqi hai?', 'Kis kis se paise lene hain?', 'Udhaar walon ki list batao', 'Khatay walon ke naam batao'.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "record_payment",
            "description": "Record a payment or balance reduction when a customer pays back money, clears part of their debt, or user asks to reduce/deduct (e.g. 'Ali ke khate me se 500 kam kar do', '500 vasool ho gaye', '500 jama kar do', '500 wapas diye', '500 minus kar do').",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_name": {"type": "string", "description": "Customer name strictly in authentic Urdu script (e.g. علی, حماد, اسلم)"},
                    "amount": {"type": "number", "description": "Amount in Pakistani Rupees to reduce/pay back"}
                },
                "required": ["customer_name", "amount"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_customer",
            "description": "Create or open a new customer account or khata.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_name": {"type": "string", "description": "Customer name strictly in authentic Urdu script (e.g. علی, حماد, اسلم)"}
                },
                "required": ["customer_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_balance_single",
            "description": "Check the outstanding udhaar/balance of a specific customer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_name": {"type": "string", "description": "Customer name strictly in authentic Urdu script (e.g. علی, حماد, اسلم)"}
                },
                "required": ["customer_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_balance_all",
            "description": "Check total outstanding udhaar/balance across all customers in the ledger.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_customer_khata",
            "description": "Delete or clear a customer's ledger/khata.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_name": {"type": "string", "description": "Customer name strictly in authentic Urdu script (e.g. علی, حماد, اسلم)"}
                },
                "required": ["customer_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "confirm_pending_action",
            "description": "Confirm or cancel a pending action when user responds with yes (haan, theek hai, likh do) or no (nahi, cancel, mat karo).",
            "parameters": {
                "type": "object",
                "properties": {
                    "confirmed": {"type": "boolean", "description": "True if confirmed, False if cancelled"}
                },
                "required": ["confirmed"]
            }
        }
    }
]

GROQ_SYSTEM_PROMPT = """You are an expert AI assistant for a Pakistani shopkeeper's digital ledger app (DigiMunshi).
The user speaks in Urdu or Roman Urdu. Call the appropriate tool based on user intent:
1. When debt/credit is ADDED or loaned: (udhaar likho, udhaar do, mazeed likh do, baqi likho, bed udhaar liya, cheeni li, rashan liya) -> call `record_udhaar`.
2. When the user asks for the list or names of customers who owe money: (gahkon ke naam batao jin ka udhaar rehta hai, kin kin ka udhaar baqi hai, kis kis se paise lene hain, udhaar walon ke naam) -> call `list_debtors`.
3. When debt is REDUCED, subtracted, or paid back: (kam kar do, minus kar do, jama kar lo, vasool ho gaye, wapas diye, paise de diye, kat lo) -> call `record_payment`.
4. When checking how much a single customer owes: (kitna udhaar hai, kitne paise hain, kitna baqi hai) -> call `query_balance_single`.
5. When checking total overall udhaar amount for all customers: (sab ka kitna hai, kul udhaar kitna hai, total baqi) -> call `query_balance_all`.
6. When wiping/clearing a khata completely: (khata clear kar do, poora mita do, khatam kar do) -> call `delete_customer_khata`.
7. When answering a confirmation prompt:
   - haan, ji, theek hai, sahi hai, likh do, kar do, haan kar do -> confirmed: true
   - nahi, cancel, mat karo, rehne do, roko -> confirmed: false

CRITICAL RULE FOR FLAWLESS URDU PRONUNCIATION:
You MUST ALWAYS output `customer_name` and `item` strictly in authentic Urdu script (نستعلیق / اردو رسم الخط) e.g. 'علی', 'حماد', 'اسلم', 'کامران', 'بیڈ', 'چینی', 'دودھ', 'راشن', 'سیمنٹ'.
NEVER output customer_name or item in Roman Urdu or English alphabet (do NOT output 'Ali', output 'علی'; do NOT output 'bed', output 'بیڈ'; do NOT output 'Hammad', output 'حماد').
Always accurately extract numerical amount in Rupees (e.g. 'paanch sau' -> 500, 'teen sau' -> 300, 'hazar' -> 1000, 'bees hazar' -> 20000)."""



def extract_urdu_amount(text: str) -> float | None:
    # 1. Match explicit digits: e.g. 500, 1000
    m = re.search(r'\b(\d+(?:\.\d+)?)\b', text)
    if m:
        return float(m.group(1))

    # 2. Number words in Urdu and Roman Urdu
    multipliers = {
        'سو': 100, 'sau': 100, 'so': 100,
        'ہزار': 1000, 'hazar': 1000, 'hazaar': 1000,
        'لاکھ': 100000, 'lakh': 100000
    }
    digits = {
        'ایک': 1, 'دو': 2, 'تین': 3, 'چار': 4, 'پانچ': 5, 'چھ': 6, 'سات': 7, 'آٹھ': 8, 'نو': 9, 'دس': 10,
        'بیس': 20, 'پچاس': 50,
        'ek': 1, 'do': 2, 'teen': 3, 'char': 4, 'paanch': 5, 'che': 6, 'saat': 7, 'aath': 8, 'nau': 9, 'das': 10,
        'bees': 20, 'pachaas': 50, 'pachas': 50
    }

    words = text.split()
    total = 0.0
    current_val = None

    for w in words:
        w_clean = w.strip('.,!؟،')
        if w_clean in digits:
            current_val = float(digits[w_clean])
        elif w_clean in multipliers:
            factor = float(multipliers[w_clean])
            if current_val is not None:
                total += current_val * factor
                current_val = None
            else:
                total += factor
        else:
            if current_val is not None:
                total += current_val
                current_val = None
    if current_val is not None:
        total += current_val

    return total if total > 0 else None


def fallback_classify_intent(transcript: str) -> dict:
    t = transcript.lower()

    # 1. list_debtors (customers with outstanding debt)
    if any(p in t for p in ["گاہکوں کے نام", "کس کس کا ادھار", "کن کن کا ادھار", "کس کس سے پیسے", "udhaar walon", "gahkon ke naam", "kin kin ka", "list batao"]):
        return {"intent": "list_debtors", "customer_name": None, "amount": None, "confidence": 0.95}

    # 1b. query_balance_all
    if any(p in t for p in ["سب کا", "کل ادھار", "sab ka", "kul udhaar", "tamam", "تمام"]):
        return {"intent": "query_balance_all", "customer_name": None, "amount": None, "confidence": 0.95}


    # 2. delete_entry
    if any(p in t for p in ["مٹا", "ختم", "ڈیلیٹ", "mita", "khatam", "delete", "clear"]):
        m = re.search(r'([\w\u0600-\u06FF]+)\s*(?:کا|کے|ki|ka|ke)\s+(?:کھاتہ|khata|entry)', transcript)
        name = m.group(1) if m else None
        return {"intent": "delete_entry", "customer_name": name, "amount": None, "confidence": 0.9}

    # 3. query_balance_single
    if any(p in t for p in ["کتنا ادھار", "کتنے پیسے", "بیلنس", "kitna udhaar", "kitne paise", "balance", "kitna hai"]):
        m = re.search(r'([\w\u0600-\u06FF]+)\s*(?:کا|کے|کو|ki|ka|ke|ko)', transcript)
        name = m.group(1) if m else None
        return {"intent": "query_balance_single", "customer_name": name, "amount": None, "confidence": 0.9}

    # 4. add_customer
    has_customer_kw = any(k in t for k in ["کسٹمر", "کھاتہ", "کھاتا", "customer", "khata"])
    has_action_kw = any(k in t for k in ["نیا", "کھولو", "بناؤ", "بنا", "ایڈ", "درج", "naya", "kholo", "banao", "bana", "add", "darj"])
    if has_customer_kw and has_action_kw:
        m = re.search(r'([\w\u0600-\u06FF]+)\s*(?:کا|کے|ke|ka)\s*(?:(?:نیا|naya)\s*)?(?:کھاتہ|کھاتا|khata)', transcript, re.IGNORECASE)
        if not m:
            m = re.search(r'(?:(?:نیا|naya)\s*(?:کسٹمر|customer)|(?:کسٹمر|customer))\s+([\w\u0600-\u06FF]+)', transcript, re.IGNORECASE)
        name = m.group(1) if m else None
        if name and name.lower() not in ["سب", "کل", "کچھ", "sab", "kul", "kholo", "banao", "add", "karo"]:
            return {"intent": "add_customer", "customer_name": name, "amount": None, "confidence": 0.9}

    # 5. record_payment (reducing debt, payment received, 'kam kar do', 'vasool', 'jama')
    amount = extract_urdu_amount(transcript)
    if amount is not None and any(p in t for p in ["کم", "وصول", "جمع", "واپس", "مائنس", "کاٹ", "kam", "vasool", "jama", "wapas", "minus", "kat", "paid"]):
        m = re.search(r'([\w\u0600-\u06FF]+)\s*(?:کو|کا|کے|میں|سے|ko|ka|ke|me|se)\b', transcript)
        name = m.group(1) if m else None
        if name in ["سب", "کل", "کچھ", "sab", "kul"]:
            name = None
        return {"intent": "record_payment", "customer_name": name, "amount": amount, "confidence": 0.95 if name else 0.8}

    # 6. add_entry
    if amount is not None or any(p in t for p in ["ادھار", "دیے", "روپے", "udhaar", "diye", "rupay", "likh"]):
        m = re.search(r'([\w\u0600-\u06FF]+)\s*(?:کو|کا|کے|ko|ka|ke)\b', transcript)
        name = m.group(1) if m else None
        if name in ["سب", "کل", "کچھ", "sab", "kul"]:
            name = None
        return {"intent": "add_entry", "customer_name": name, "amount": amount, "confidence": 0.95 if name and amount else 0.75}

    return {"intent": "unknown", "customer_name": None, "amount": None, "confidence": 0.0}


async def classify_intent(transcript: str) -> dict:
    """Classify intent using Groq LLM tool calling with fallback to rule-based parser."""
    if not transcript or not transcript.strip():
        return {"intent": "unknown", "customer_name": None, "amount": None, "confidence": 0.0}

    if settings.GROQ_API_KEY:
        try:
            client = AsyncGroq(api_key=settings.GROQ_API_KEY)
            response = await client.chat.completions.create(
                model="openai/gpt-oss-120b",
                messages=[
                    {"role": "system", "content": GROQ_SYSTEM_PROMPT},
                    {"role": "user", "content": transcript}
                ],
                tools=GROQ_TOOLS,
                tool_choice="auto",
                temperature=0.0
            )
            msg = response.choices[0].message
            if msg.tool_calls:
                tc = msg.tool_calls[0]
                fn_name = tc.function.name
                args = json.loads(tc.function.arguments or "{}")
                logger.info(f"Groq agent selected tool: {fn_name}")

                if fn_name == "record_udhaar":
                    return {
                        "intent": "add_entry",
                        "customer_name": args.get("customer_name"),
                        "amount": float(args["amount"]) if args.get("amount") is not None else None,
                        "item": args.get("item"),
                        "confidence": 0.98
                    }
                elif fn_name == "list_debtors":
                    return {
                        "intent": "list_debtors",
                        "customer_name": None,
                        "amount": None,
                        "confidence": 0.98
                    }
                elif fn_name == "record_payment":
                    return {
                        "intent": "record_payment",
                        "customer_name": args.get("customer_name"),
                        "amount": float(args["amount"]) if args.get("amount") is not None else None,
                        "confidence": 0.98
                    }

                elif fn_name == "create_customer":
                    return {
                        "intent": "add_customer",
                        "customer_name": args.get("customer_name"),
                        "amount": None,
                        "confidence": 0.98
                    }
                elif fn_name == "query_balance_single":
                    return {
                        "intent": "query_balance_single",
                        "customer_name": args.get("customer_name"),
                        "amount": None,
                        "confidence": 0.98
                    }
                elif fn_name == "query_balance_all":
                    return {
                        "intent": "query_balance_all",
                        "customer_name": None,
                        "amount": None,
                        "confidence": 0.98
                    }
                elif fn_name == "delete_customer_khata":
                    return {
                        "intent": "delete_entry",
                        "customer_name": args.get("customer_name"),
                        "amount": None,
                        "confidence": 0.95
                    }
                elif fn_name == "confirm_pending_action":
                    is_confirmed = args.get("confirmed", True)
                    return {
                        "intent": "confirm" if is_confirmed else "cancel",
                        "customer_name": None,
                        "amount": None,
                        "confidence": 0.98,
                        "confirmed": is_confirmed
                    }
        except Exception as e:
            logger.warning(f"Groq agent classification failed, using fallback: {e}")

    return fallback_classify_intent(transcript)
