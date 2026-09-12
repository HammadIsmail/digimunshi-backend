# DigiMunshi Backend 🎙️💼

> **AI-First Voice Ledger Engine for Pakistani Micro-Merchants & Shopkeepers**

DigiMunshi Backend is a high-performance voice accounting and digital khata API. It enables small shopkeepers and kiryana store owners to manage their credit and payments naturally using conversational Urdu and Roman Urdu voice commands.

- 📱 **Mobile Frontend Repository**: [digimunshi-mobile](https://github.com/HammadIsmail/digimunshi-mobile)

---

## 🌟 Key Features

- **Urdu-Native Voice Processing**: Accurately transcribes spoken Urdu (audio formats: WAV, M4A) via Uplift AI Scribe STT.
- **Ultra-Fast LLM Tool Agent**: Powered by Groq's high-speed inference engine running `llama-3.3-70b-versatile` with structured tool calling.
  - Understands colloquial Urdu number phrases (*"پانچ سو"*, *"ڈھائی ہزار"*, *"ek hazar"*, *"dedh sau"*).
  - Handles credit additions (`record_udhaar`) with item descriptions (*"بیڈ"*, *"چینی"*, *"راشن"*).
  - Handles debt reductions and payments (`record_payment` / *"kam kar do"*, *"vasool ho gaye"*).
  - Supports verbal debtor breakdowns (`list_debtors` / *"kin kin ka udhaar baqi hai"*).
  - Supports balance queries (`query_balance_single`, `query_balance_all`) and account clearance (`delete_customer_khata`).
- **Urdu Voice Synthesis & Phonetic Guardrail**: Synthesizes speech strictly in authentic Urdu script (نستعلیق) via Uplift AI Orator (`prime-time-anchor` voice) with fallback transliteration (`to_urdu_script`) for natural native pronunciation.
- **Stateless Base64 Audio**: Returns audio directly as Base64 data URIs, eliminating disk dependencies on ephemeral cloud hosts (e.g. Render).
- **PostgreSQL Ledger**: ACID-compliant transactional double-entry ledger with automatic balance tracking and audit trails on Neon Serverless Postgres.
- **Safety Guardrails**: Two-step confirmation for returning customer debt additions, high-value amounts, and khata clearance.

---

## 🛠️ Tech Stack

- **Framework**: [FastAPI](https://fastapi.tiangolo.com/) (Python 3.11+)
- **Database**: PostgreSQL with async [SQLAlchemy 2.0](https://www.sqlalchemy.org/) & [asyncpg](https://github.com/MagicStack/asyncpg) (Neon connection pool resilience)
- **AI / LLM**: [Groq](https://groq.com/) API (`llama-3.3-70b-versatile` LPU inference engine)
- **Voice AI**: [Uplift AI](https://upliftai.org/) (Scribe STT + Orator Urdu TTS)
- **Authentication**: JWT (JSON Web Tokens) with bcrypt password hashing
- **Deployment**: Render-ready with `.python-version` (Python 3.11.9)

---

## 🚀 Getting Started

### 1. Prerequisites
- Python 3.11+
- PostgreSQL database (or Neon Serverless Postgres)
- Groq API Key
- Uplift AI API Key

### 2. Installation

```bash
# Clone repository
git clone https://github.com/HammadIsmail/digimunshi-backend.git
cd digimunshi-backend

# Create and activate virtual environment
python -m venv .venv
# On Windows:
.venv\Scripts\activate
# On Linux/macOS:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Environment Configuration

Create a `.env` file in the `backend/` directory:

```env
DATABASE_URL=postgresql+asyncpg://<username>:<password>@<host>/<database>?ssl=require
JWT_SECRET_KEY=your_super_secret_jwt_key
GROQ_API_KEY=gsk_...
GROQ_MODEL=llama-3.3-70b-versatile
UPLIFTAI_API_KEY=your_uplift_api_key
UPLIFTAI_API_URL=https://api.upliftai.org
UPLIFTAI_TTS_VOICE=prime-time-anchor
CORS_ORIGINS=["http://localhost:8081","http://localhost:19006"]
```

### 4. Run the Server

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Interactive API documentation will be available at:
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

---

## 📁 Project Architecture

```
backend/
├── app/
│   ├── core/
│   │   ├── config.py         # App settings & environment validation
│   │   ├── database.py       # Async database engine & session factory
│   │   ├── deps.py           # Dependency injection (Auth, Current User)
│   │   └── security.py       # JWT creation & password hashing
│   ├── models/
│   │   └── models.py         # SQLAlchemy ORM models (Shop, Customer, LedgerEntry)
│   ├── routers/
│   │   ├── auth.py           # Shopkeeper register, login & refresh
│   │   ├── ledger.py         # Customer lists, balances & transaction history
│   │   └── voice.py          # Voice upload, processing & confirmation flows
│   ├── schemas/
│   │   └── schemas.py        # Pydantic request/response validation schemas
│   ├── services/
│   │   ├── intent.py         # Groq LLM tool agent & fallback classifier
│   │   ├── ledger.py         # Ledger calculations & customer lookups
│   │   └── voice.py          # Uplift STT & TTS audio processing
│   └── main.py               # FastAPI application factory & middleware
├── requirements.txt
└── README.md
```

---

## 🔒 Security

- All sensitive keys (`.env`) are excluded from version control via `.gitignore`.
- Password and PIN codes are stored using salted bcrypt hashing.
- Token-based stateless authentication with short-lived access tokens and refresh tokens.

---

## 📄 License
This project is licensed under the MIT License.
