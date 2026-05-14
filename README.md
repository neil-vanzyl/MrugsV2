# Accedo Strategic Lead Scout

An AI-powered OTT sales intelligence pipeline that researches, qualifies, and writes personalised outreach for streaming industry prospects. Built for Accedo's Director of Strategic Accounts.

---

## How It Works

The pipeline runs in six stages per prospect:

1. **Gemini + Exa** — discovers relevant companies from LinkedIn before Grok runs
2. **Grok-4** — deep research waterfall (SEC filings, job boards, app stores, press, X/Twitter)
3. **Apollo** — validates power map contacts and enriches with verified emails and LinkedIn URLs
4. **Exa** — fetches LinkedIn post intelligence per exec for personalised outreach openers
5. **Claude Sonnet** — qualifies each prospect with a score and HOT/WARM/COLD verdict
6. **Claude Opus** — drafts two personalised outreach emails per prospect
7. **Google Sheets** — writes results to Leads or Cold Leads tab with full intelligence

---

## Prerequisites

- Python 3.11 or 3.12
- A Google Cloud service account with Sheets + Drive API access
- API keys for: xAI (Grok), Anthropic (Claude), Google (Gemini), Exa, Apollo (two keys)

---

## Local Setup

### 1. Clone the repository

```bash
git clone https://github.com/your-org/streamlit-sales-lead-generator.git
cd streamlit-sales-lead-generator
```

### 2. Create a virtual environment

```bash
python3 -m venv venv
source venv/bin/activate        # Mac/Linux
venv\Scripts\activate           # Windows
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
pip install exa-py              # LinkedIn intelligence SDK
```

### 4. Create your `.env` file

Copy the example below and fill in your keys:

```bash
# Required
XAI_API_KEY=xai-...
ANTHROPIC_API_KEY=sk-ant-...
GEMINI_API_KEY=...

# Google Sheets
GOOGLE_SERVICE_ACCOUNT_JSON=service_account.json
GOOGLE_SHEET_NAME=OTT Leads
GOOGLE_WORKSHEET_NAME=Leads
GOOGLE_COLD_WORKSHEET_NAME=Cold Leads
GOOGLE_LOGS_WORKSHEET_NAME=Logs

# Optional but recommended
EXA_API_KEY=...
APOLLO_MASTER_API_KEY=...      # Master key — for People Search (zero credits)
APOLLO_API_KEY=...             # Standard key — for Bulk Enrichment (1 credit/person)
```

### 5. Add your Google service account

Download your service account JSON from Google Cloud Console and save it as `service_account.json` in the project root.

Make sure the service account has been granted **Editor** access to your Google Sheet.

### 6. Run the Streamlit app

```bash
streamlit run gui.py
```

The app will open at `http://localhost:8501`.

---

## Apollo Key Setup

Two separate Apollo keys are required because they call different endpoints:

| Key | Type | Endpoint | Credits |
|-----|------|----------|---------|
| `APOLLO_MASTER_API_KEY` | Master | `/mixed_people/api_search` | Zero |
| `APOLLO_API_KEY` | Standard | `/people/bulk_match` | 1 per person |

Generate both at: `app.apollo.io → Settings → Integrations → API Keys`

---

## Running via CLI

```bash
# Single query
python main.py --query "Regional sports broadcaster migrating from ViewLift 2026"

# Dry run (no Sheets writes)
python main.py --query "..." --dry-run

# Research specific named companies
python main.py --prospect "FuboTV" --prospect "Sling TV"

# Debug logging
python main.py --query "..." --debug
```

---

## Project Structure

```
├── gui.py                  # Streamlit front-end
├── main.py                 # Pipeline orchestration
├── config.py               # All configuration and API keys
├── requirements.txt
├── suggested_prompts.txt   # Pre-built discovery prompts
├── service_account.json    # Google credentials (not committed)
│
├── core/
│   └── sheets.py           # Google Sheets persistence
│
├── tools/
│   ├── grok.py             # xAI Grok research waterfall
│   ├── gemini.py           # Gemini query translation + company scoring
│   ├── discovery.py        # Pre-Grok company discovery orchestration
│   ├── apollo.py           # Apollo contact enrichment
│   ├── exa.py              # LinkedIn post intelligence
│   └── claude_client.py    # Claude Sonnet (analyst) + Opus (copywriter)
│
├── prompts/
│   ├── scout.py            # Grok research waterfall prompt
│   ├── analyst.py          # Claude Sonnet qualification prompt
│   ├── copywriter.py       # Claude Opus outreach drafting prompt
│   └── gemini_scorer.py    # Gemini query translation + scoring prompts
│
└── utils/
    ├── helpers.py          # Logging, retries, rate limiting
    └── usage_tracker.py    # Per-run API cost tracking
```

---

## Streamlit Cloud Deployment

1. Push your repo to GitHub
2. Go to [share.streamlit.io](https://share.streamlit.io) and connect your repo
3. Set `gui.py` as the main file
4. Add all secrets under **Manage app → Secrets** in TOML format:

```toml
XAI_API_KEY = "xai-..."
ANTHROPIC_API_KEY = "sk-ant-..."
GEMINI_API_KEY = "..."
EXA_API_KEY = "..."
APOLLO_MASTER_API_KEY = "..."
APOLLO_API_KEY = "..."
GOOGLE_SHEET_NAME = "OTT Leads"
GOOGLE_WORKSHEET_NAME = "Leads"
GOOGLE_COLD_WORKSHEET_NAME = "Cold Leads"
GOOGLE_LOGS_WORKSHEET_NAME = "Logs"

[GOOGLE_SERVICE_ACCOUNT_JSON]
type = "service_account"
project_id = "your-project-id"
private_key_id = "..."
private_key = "-----BEGIN RSA PRIVATE KEY-----\n..."
client_email = "..."
token_uri = "https://oauth2.googleapis.com/token"
```

---

## Verify Your Setup

Run a syntax check across all files before pushing:

```bash
python3 -m compileall files/ -x files/.git
```

No output on a file means it passed cleanly.