# AI Assistant for Odoo 18 — Complete Installation & Running Guide

## What You're Building

An AI chat assistant INSIDE Odoo that:
- Takes natural language questions ("Show me contacts with gmail")
- Detects intent → maps to the right Odoo model (res.partner)
- Fetches REAL data via Odoo's ORM (respects user permissions)
- Injects that data into an AI prompt (never lets AI guess)
- Returns accurate, conversational answers
- Keeps all data LOCAL (using Ollama)

---

## Prerequisites

Before starting, you need:

| Requirement | Why | How to check |
|-------------|-----|--------------|
| Python 3.10+ | Odoo 18 requires it | `python3 --version` |
| PostgreSQL 14+ | Odoo's database | `psql --version` |
| Odoo 18 source | The ERP itself | You already have this |
| Ollama | Local AI runtime | We'll install this |
| 16GB+ RAM | For running the LLM | Check system specs |
| (Optional) NVIDIA GPU | Faster AI responses | `nvidia-smi` |

---

## Step 1: Install PostgreSQL

### On Ubuntu/Debian:
```bash
sudo apt update
sudo apt install postgresql postgresql-client
sudo systemctl start postgresql
sudo systemctl enable postgresql
```

### On Windows:
Download from https://www.postgresql.org/download/windows/
Install with default settings. Remember the password you set.

### On macOS:
```bash
brew install postgresql@15
brew services start postgresql@15
```

### Create the Odoo database user:
```bash
# Switch to postgres user
sudo -u postgres psql

# Inside psql:
CREATE USER odoo WITH CREATEDB PASSWORD 'odoo';
ALTER USER odoo WITH SUPERUSER;
\q
```

**What this does:** PostgreSQL is where ALL your Odoo data lives — contacts, leads, sales, everything. Odoo talks to PostgreSQL, and our AI module talks to Odoo's ORM (which talks to PostgreSQL). Your data never bypasses Odoo's security layer.

---

## Step 2: Install Odoo 18 Python Dependencies

```bash
cd /path/to/odoo

# Create a virtual environment (recommended)
python3 -m venv odoo-venv
source odoo-venv/bin/activate   # Linux/macOS
# OR on Windows:
# odoo-venv\Scripts\activate

# Install Odoo's requirements
pip install -r requirements.txt

# Install additional requirement for our AI module
pip install requests
```

**What this does:** Sets up an isolated Python environment with all libraries Odoo needs. The `requests` library is what our module uses to talk to Ollama's HTTP API on localhost.

---

## Step 3: Install Ollama (Local AI)

### On Linux:
```bash
curl -fsSL https://ollama.com/install.sh | sh
```

### On Windows:
Download from https://ollama.com/download/windows

### On macOS:
```bash
brew install ollama
```

### Pull a model:
```bash
# Start Ollama service
ollama serve

# In another terminal — download the AI model
# Choose ONE based on your hardware:

# Option A: Lightweight (needs ~5GB RAM, fast, decent quality)
ollama pull llama3.1:8b

# Option B: Powerful (needs ~40GB RAM, slower, excellent quality)
ollama pull llama3.1:70b

# Option C: Very lightweight (needs ~2GB RAM, fastest, basic quality)
ollama pull phi3:mini
```

### Verify it works:
```bash
# Test that Ollama responds
curl http://localhost:11434/api/tags

# Should return JSON with your downloaded model listed

# Quick chat test
ollama run llama3.1 "Say hello in one sentence"
```

**What this does:** Ollama is a local AI runtime. It downloads and runs AI models entirely on YOUR machine. When our Odoo module sends a question to Ollama, it goes to `http://localhost:11434` — that's a local-only address. The data physically cannot leave your computer/server. This is the KEY to your privacy requirement.

**How it compares to ChatGPT:**
- ChatGPT: Your data → Internet → OpenAI servers (USA) → response back
- Ollama: Your data → localhost → your own CPU/GPU → response back (NEVER leaves)

---

## Step 4: Configure Odoo

Create an Odoo configuration file:

```bash
# Create config file
cat > /path/to/odoo/odoo.conf << 'EOF'
[options]
; Database
db_host = localhost
db_port = 5432
db_user = odoo
db_password = odoo
db_name = odoo_ai_demo

; Server
http_port = 8069
admin_passwd = admin

; Addons — make sure our module directory is included
addons_path = /path/to/odoo/addons,/path/to/odoo/odoo/addons

; Development mode (auto-reload on file changes)
dev = xml,reload
EOF
```

**Replace `/path/to/odoo`** with your actual Odoo path (e.g., `/c/workspace/odoo`).

**What each setting does:**
- `db_host/port/user/password` → How Odoo connects to PostgreSQL
- `db_name` → The database name Odoo will create
- `http_port = 8069` → Odoo's web interface will be at http://localhost:8069
- `addons_path` → Where Odoo looks for modules (our `sdlc_ai_assistant` is in `addons/`)
- `dev = xml,reload` → Auto-reload when you change Python/XML files (development only)

---

## Step 5: Initialize the Odoo Database

```bash
cd /path/to/odoo

# Create the database and install base modules
python3 odoo-bin -c odoo.conf -d odoo_ai_demo -i base --stop-after-init
```

**What this does:** Creates a fresh PostgreSQL database called `odoo_ai_demo` and installs Odoo's core (`base` module). The `--stop-after-init` flag means "set up and exit" — useful for initial setup.

**What happens inside:**
1. Odoo connects to PostgreSQL
2. Creates 400+ database tables (res_partner, crm_lead, etc.)
3. Loads base data (countries, currencies, default settings)
4. Creates the admin user (login: admin, password: admin)

---

## Step 6: Install Required Odoo Apps

Our AI module depends on CRM, Sales, HR, Inventory, and Accounting. Install them:

```bash
python3 odoo-bin -c odoo.conf -d odoo_ai_demo \
    -i crm,sale,hr,stock,account,contacts \
    --stop-after-init
```

**What this does:** Installs the Odoo apps that our AI assistant will query:
- `crm` → Creates `crm.lead` table (leads/opportunities)
- `sale` → Creates `sale.order` table (quotations/orders)
- `hr` → Creates `hr.employee` table (employees)
- `stock` → Creates stock/inventory tables
- `account` → Creates `account.move` table (invoices)
- `contacts` → Enhanced contact management views

Each module creates its own database tables, security rules, and menu items.

---

## Step 7: Install the AI Assistant Module

```bash
python3 odoo-bin -c odoo.conf -d odoo_ai_demo \
    -i sdlc_ai_assistant \
    --stop-after-init
```

**What happens when this runs:**

1. Odoo reads `__manifest__.py` → sees dependencies (crm, sale, hr, etc.)
2. Checks all dependencies are installed → ✓
3. Creates NEW database tables:
   - `ai_config` → Stores Ollama/OpenAI connection settings
   - `ai_intent` → Maps keywords to Odoo models
   - `ai_conversation` → Chat history per user
   - `ai_message` → Individual messages with audit data
4. Loads `security/ai_security.xml` → Creates user groups:
   - "AI Assistant User" → Can use the chat
   - "AI Assistant Manager" → Can configure settings + view all conversations
5. Loads `security/ir.model.access.csv` → Sets permissions:
   - Users can read config, read/write conversations
   - Managers can read/write everything
6. Loads `data/ai_intent_data.xml` → Creates 7 pre-configured intents:
   - "contacts" → res.partner
   - "leads" → crm.lead
   - "sales" → sale.order
   - "employees" → hr.employee
   - "departments" → hr.department
   - "products" → product.template
   - "invoices" → account.move
7. Loads views → Creates menus, forms, list views
8. Loads JS/XML assets → Registers the chat interface

---

## Step 8: Start Odoo

Now run Odoo normally (without --stop-after-init):

```bash
# Make sure Ollama is running in another terminal:
# ollama serve

# Start Odoo
python3 odoo-bin -c odoo.conf -d odoo_ai_demo
```

You should see:
```
INFO odoo_ai_demo odoo.modules.loading: 42 modules loaded in 3.50s
INFO odoo_ai_demo odoo.http: HTTP service (Werkzeug) running on 0.0.0.0:8069
```

**Open your browser:** http://localhost:8069

**Login:**
- Email: `admin`
- Password: `admin`

---

## Step 9: Configure the AI Assistant

### 9a. Assign yourself the AI Manager role

1. Go to **Settings** → **Users & Companies** → **Users**
2. Click on your user (Administrator)
3. Scroll to **Productivity** section
4. Set **AI Assistant** to "AI Assistant Manager"
5. Click **Save**

**Why:** Only managers can access AI configuration. Regular users can only chat.

### 9b. Configure Ollama connection

1. Click **AI Assistant** in the top menu bar
2. Go to **Configuration** → **AI Settings**
3. Click **Create** (or edit the existing one)
4. Set:
   - **Name:** Default Configuration
   - **Provider:** Ollama (Local — Privacy Safe)
   - **Ollama URL:** `http://localhost:11434`
   - **Ollama Model:** `llama3.1` (or whichever model you pulled)
   - **Temperature:** `0.3` (lower = more factual)
   - **Max Tokens:** `1024`
   - **Timeout:** `60`
5. Click **Save**
6. Click **Test Connection** button

**What "Test Connection" does:** Sends a tiny test message to Ollama → if you see "Connection successful!" it means Odoo can talk to the local AI.

If it fails, check:
- Is Ollama running? (`ollama serve` in another terminal)
- Is the URL correct? (default: `http://localhost:11434`)
- Did you pull the model? (`ollama list` to check)

---

## Step 10: Add Demo Data (so you have something to ask about)

The AI assistant answers questions about YOUR data. If the database is empty, there's nothing to answer. Let's add some data.

### Option A: Load Odoo's demo data (recommended for testing)

```bash
# Stop Odoo first (Ctrl+C)
# Re-initialize with demo data
python3 odoo-bin -c odoo.conf -d odoo_ai_demo \
    -i crm,sale,hr,stock,account,contacts,sdlc_ai_assistant \
    --stop-after-init --without-demo=False
```

This loads sample contacts, leads, sales orders, employees, etc.

### Option B: Add data manually through the UI

1. **Contacts:** Go to Contacts → Create → Add a few contacts with emails
2. **Leads:** Go to CRM → Create → Add a few leads
3. **Employees:** Go to Employees → Create → Add a few employees
4. **Sales:** Go to Sales → Orders → Create → Make a quotation

---

## Step 11: Start Chatting!

1. Click **AI Assistant** in the top menu
2. Click **Chat**
3. You'll see the chat interface with example queries
4. Type a question and press Enter

### Example conversation:

```
You: Show me all contacts
AI:  I found 15 contacts in your system. Here are the top 10:
     1. Azure Interior — azure@example.com — San Francisco
     2. Deco Addict — deco@example.com — Las Vegas
     3. Gemini Furniture — gemini@example.com — New York
     ...

You: Which ones have gmail?
AI:  I found 3 contacts with Gmail addresses:
     1. John Smith — john.smith@gmail.com
     2. Sarah Connor — sarah.c@gmail.com
     3. Mike Johnson — mike.j@gmail.com

You: Show me leads this month
AI:  Here are your CRM leads created this month (March 2026):
     1. "Website Redesign" — Expected Revenue: $15,000 — Stage: New
     2. "Annual Contract Renewal" — Expected Revenue: $8,500 — Stage: Qualified
     ...

You: How many employees do we have?
AI:  Based on the data, you have 12 employees across these departments:
     - Sales: 4 employees
     - Engineering: 3 employees
     - HR: 2 employees
     - Marketing: 3 employees
```

---

## What Happens Behind the Scenes (Full Trace)

When you type **"Show me contacts with gmail"** and press Enter:

### Frontend (Browser)
```
1. ai_chat_action.js → onSend()
2. Sends JSON-RPC POST to /ai_assistant/ask
   Body: {"query": "Show me contacts with gmail", "conversation_id": null}
```

### Controller Layer (ai_controller.py)
```
3. AiAssistantController.ask() receives the request
4. Calls request.env['ai.assistant'].ask(query="Show me contacts with gmail")
```

### Orchestrator (ai_assistant.py)
```
5. Creates new ai.conversation record (id=1)
6. Saves user message to ai.message table
7. Calls env['ai.intent'].detect_intent("Show me contacts with gmail")
```

### Intent Detection (ai_intent.py)
```
8. Loads all active intents from database
9. Scores each intent against the query:
   - "Contacts" intent: "contacts" found → score 3, word boundary match → +2 = 5
   - "Employees" intent: no keyword match → score 0
   - "CRM Leads" intent: no keyword match → score 0
   Winner: "Contacts" (score 5, confidence 0.5)

10. Parses filters from query:
    "with gmail" matches FILTER_PATTERNS[0]: _filter_email_or_domain
    → Returns domain: [('email', 'ilike', 'gmail')]

11. Returns: {
      model: 'res.partner',
      fields: ['name', 'email', 'phone', 'city', 'country_id', 'company_name', 'function'],
      domain: [('email', 'ilike', 'gmail')],
      limit: 10,
      confidence: 0.5,
    }
```

### Data Fetching (ai_data_fetcher.py)
```
12. Receives intent result
13. Validates model exists: env['res.partner'] → ✓
14. Checks user has read access: check_access_rights('read') → ✓
15. Sanitizes fields: removes any blacklisted fields (password, etc.)
16. Executes ORM query:
    env['res.partner'].search([('email', 'ilike', 'gmail')], limit=10)
    → Odoo automatically applies record rules (user only sees allowed records)
17. Converts records to dicts:
    [{'name': 'John', 'email': 'john@gmail.com', 'phone': '555-0123', ...}, ...]
18. Formats as context string:
    """
    === Odoo Data: Contacts / Partners ===
    Source: res.partner
    Records shown: 3 of 3 total
    Fields: name, email, phone, city, country_id, company_name, function

    --- Record 1 ---
      Name: John Smith
      Email: john@gmail.com
      Phone: +1-555-0123
      City: New York
    ...
    """
```

### AI Provider (ai_provider.py)
```
19. Loads active config: provider='ollama', url='http://localhost:11434'
20. Builds messages array:
    [
      {role: "system", content: "You are an AI assistant integrated with Odoo ERP...
       Answer ONLY based on the Odoo data provided below..."},
      {role: "user", content: "User Question: Show me contacts with gmail\n\n
       === Odoo Data: Contacts / Partners ===\n
       Source: res.partner\nRecords shown: 3 of 3 total\n...\n\n
       Instructions: Answer ONLY using the data above."}
    ]
21. Sends HTTP POST to http://localhost:11434/api/chat
    → This is LOCALHOST — data stays on your machine
22. Ollama processes with llama3.1 model
23. Returns: "I found 3 contacts with Gmail addresses:\n1. John Smith..."
```

### Response Handling (back in ai_assistant.py)
```
24. Saves assistant message to ai.message table:
    - content: "I found 3 contacts with Gmail..."
    - model_accessed: "res.partner"
    - records_accessed: 3
    - tokens_used: 142
    - response_time: 2.3s
25. Returns response to controller → to frontend → displayed in chat UI
```

---

## Troubleshooting

### "Cannot connect to Ollama"
```bash
# Check if Ollama is running
curl http://localhost:11434/api/tags

# If not, start it
ollama serve

# Check if model is downloaded
ollama list
```

### "Model not found"
```bash
# The model name in Odoo config must match exactly
ollama list
# Shows: llama3.1:latest

# In Odoo config, use: llama3.1
# (Ollama adds ":latest" automatically)
```

### "Access Denied" errors
- Make sure your user has the "AI Assistant User" or "AI Assistant Manager" group
- Check Settings → Users → Your User → Productivity section

### "No intent matched"
- Your question didn't match any keywords
- Go to Configuration → Intent Mappings to see/add keywords
- Example: if you ask "show me clients" but "clients" isn't a keyword for contacts, add it

### Slow responses
- CPU-only inference is slow (10-30 seconds per response)
- Solutions:
  - Use a smaller model: `ollama pull phi3:mini`
  - Add a GPU: NVIDIA with 8GB+ VRAM
  - Reduce max_tokens in AI Settings (e.g., 512 instead of 1024)

### Empty responses from AI
- Check if there's actual data in Odoo for that query
- Try a simpler query first: "Show me contacts"
- Check Conversation History for error messages

---

## Security Checklist

Before deploying to production:

- [ ] Ollama URL is `http://localhost:11434` (NOT a public IP)
- [ ] Firewall blocks external access to port 11434
- [ ] Only "AI Assistant Manager" group users can change config
- [ ] Regular users have "AI Assistant User" group only
- [ ] OpenAI provider is NOT enabled (unless explicitly approved)
- [ ] Privacy warning checkbox is required for cloud providers
- [ ] PostgreSQL password is not the default "odoo"
- [ ] Odoo admin password is changed from default "admin"
