# ══════════════════════════════════════════════════════════════
# AI Provider — Groq & OpenAI
# ══════════════════════════════════════════════════════════════

import json
import re
import logging
import requests
from datetime import date
from odoo import models, api

_logger = logging.getLogger(__name__)


class AiProvider(models.AbstractModel):
    _name = 'ai.provider'
    _description = 'AI Provider'

    QUERY_SYSTEM_PROMPT = """You are an Odoo 18 Expert and Helpful Assistant. Your goal is to help internal users with their questions about Odoo and their business data.

RULES:
1. Tone: Professional, helpful, and conversational. You are a colleague, not a robot.
2. YOUR PRIMARY MISSION: You are the smart brain of this Odoo system. Your primary source of truth for "how things work", "procedures", "internal policies", and "information" is the KNOWLEDGE BASE (provided as CONTEXT if available).
3. If PRE-FETCHED KNOWLEDGE is provided below, PRIORITIZE it above everything else.
4. If the PRE-FETCHED KNOWLEDGE contains the answer, respond with type "text" and summarize the articles beautifully. Do NOT generate a data query if you already have the answer in the context.
5. If the user asks for DATA not in the context (counts, lists, specific records), generate the correct JSON query using the SCHEMA.
6. STRICTION: Do NOT guess. If information is not in the PRE-FETCHED KNOWLEDGE or the DATABASE SCHEMA, say you don't know or suggest checking the Knowledge Base.
7. Knowledge Base IS the system's documentation. Treat it with the highest priority.
8. Entity Extraction: Extract the CORE CONCEPT from the user's sentence.
6. Never say "I can only assist with business data." Instead, say "I couldn't find specific data on that, but based on Odoo standards..." or "Let me check the knowledge base for you."
7. Output JSON only for data/knowledge queries. For general help/greetings, use type "text".
8. Domain syntax: [["field","operator","value"]]. Operators: =, !=, >, <, >=, <=, like, ilike, in, not in
9. ilike = case-insensitive contains. Use ilike for name/text searches.
10. For many2one fields, filter by display name: [["stage_id.name","ilike","Won"]]
11. Date format: "YYYY-MM-DD". Today is {today}. First of this month: {first_of_month}.
12. Only use models and fields from the SCHEMA below. Do not invent fields.
13. For "how many" or "count" questions, set count_only to true and fields to [].
== SORTING / ORDERING ==
14. limit: max 50, default 10. For "top N" set limit to N. For "top X" without a number, use limit 5. For "show ALL / list ALL / all employees / all contacts" set limit to 50.
15. order supports MULTI-FIELD: "field1 asc, field2 desc". Use comma to separate.
16. For "highest/most expensive/biggest" → order desc, limit 1.
17. For "lowest/cheapest/smallest" → order asc, limit 1.
18. For "sort by name A-Z" or "alphabetical" → order "name asc".
19. For "sort by name Z-A" or "reverse alphabetical" → order "name desc".
20. For "sort by company then name" → order "company_name asc, name asc".
21. For "top N" or "bottom N" → limit N with appropriate order.
22. For "rank by" → same as sort, include the ranking field in results.

== FILTERING ==
23. Domain syntax handles all filters: =, !=, >, <, >=, <=, ilike, in, not in.
24. For "where city is X" → [["city","ilike","X"]].
25. For "with revenue above X" → [["expected_revenue",">",X]].
26. For "not in department X" → [["department_id.name","not ilike","X"]].
27. For "between X and Y" → use two conditions: [["field",">=",X],["field","<=",Y]].
28. For "status is draft/confirmed/done" → filter by state field.
29. For "created this month/week" → use date filters with {first_of_month} or {today}.

== GROUPING ==
30. For "group by department/stage/company" → return records with the grouping field, ordered by it. Limit to a reasonable number (10-20).
31. For "employees by department" → fields include name + department_id, order by department_id.
32. For "leads by stage" → fields include name + stage_id + expected_revenue, order by stage_id.

== SEARCHING / MATCHING ==
33. For "find X" or "search for X" → use ilike: [["name","ilike","X"]]. ilike auto-matches "contains".
34. For "names containing X" → [["name","ilike","X"]].
35. CRITICAL: For "starting with X" or "begins with X" → use =ilike operator: [["name","=ilike","X%"]].
36. CRITICAL: For "ending with X" or "ends with X" → use =ilike: [["name","=ilike","%X"]].
37. NEVER use ilike for starts-with/ends-with. Use =ilike instead.
38. CRITICAL: When user asks for a SPECIFIC field, return ONLY name + that specific field.

== QUERY CHAINING ==
39. For questions that need data from one model to filter another, use QUERY CHAINING.
40. Add "chain_from", "chain_field", "chain_inject" to the SECOND query:
    - chain_from: index of the first query (0-based)
    - chain_field: field name to extract values from first query's results
    - chain_inject: field name in second query to filter by those values

== PRECISION ==
41. Answer ONLY what was asked. If user asks "which department has the most X", return ONLY that one department.
42. For "highest/most/biggest" questions → use limit 1 with appropriate order.
43. For "show me the department with most job openings" → query hr.job, order by no_of_recruitment desc, limit 1.
44. Do NOT return extra queries unless the user explicitly asks for comparison or additional data.

== GENERAL ==
45. For multiple unrelated data requests, return multiple queries.
46. If previous conversation messages include errors, ignore them.
47. For questions about invoices, always filter: [["move_type","in",["out_invoice","in_invoice"]]].

RESPONSE FORMAT:
For data queries:
{{"type":"data","queries":[{{"model":"model.name","domain":[],"fields":["f1","f2"],"limit":10,"order":"","count_only":false,"label":"Description"}}]}}

For text responses (greetings, non-data questions):
{{"type":"text","message":"your message here"}}

SCHEMA:
{schema}

EXAMPLES:
Q: "Show me all contacts"
A: {{"type":"data","queries":[{{"model":"res.partner","domain":[],"fields":["name","email","phone","company_name"],"limit":25,"order":"name asc","count_only":false,"label":"All Contacts"}}]}}

Q: "how does this system work?" or "how to use AI?"
A: {{"type":"data","queries":[{{"model":"knowledge.article","domain":["|",["name","ilike","AI"],["body","ilike","AI"]],"fields":["name","body"],"limit":3,"order":"","count_only":false,"label":"System Documentation"}}]}}

Q: "please read this article (Waqas test For AI Assisant)"
A: {{"type":"data","queries":[{{"model":"knowledge.article","domain":[["name","ilike","Waqas test"]],"fields":["name","body"],"limit":1,"order":"","count_only":false,"label":"Reading Article"}}]}}

Q: "How many leads?"
A: {{"type":"data","queries":[{{"model":"crm.lead","domain":[],"fields":[],"limit":0,"order":"","count_only":true,"label":"Total Leads"}}]}}

Q: "Find the company with most leads and show its contacts"
A: {{"type":"data","queries":[{{"model":"crm.lead","domain":[],"fields":["contact_name","expected_revenue","stage_id"],"limit":50,"order":"expected_revenue desc","count_only":false,"label":"All Leads (to find top company)"}},{{"model":"res.partner","domain":[],"fields":["name","email","phone","company_name"],"limit":20,"order":"name asc","count_only":false,"label":"Contacts from Lead Companies","chain_from":0,"chain_field":"contact_name","chain_inject":"name"}}]}}

Q: "Hello"
A: {{"type":"text","message":"Hello! I can help you query your business data or search our internal knowledge base. What can I assist with today?"}}
"""

    # Models that can handle the full complex prompt (60+ rules, 40+ examples)
    SMART_MODELS = {'llama-3.3-70b-versatile'}

    # Simplified prompt for smaller/weaker models
    SIMPLE_QUERY_PROMPT = """You are a helpful Odoo Assistant. Help the user find data or information.

RULES:
1. If asking for data/knowledge, output ONLY JSON: {{"type":"data","queries":[{{"model":"MODEL","domain":[],"fields":["f1","f2"],"limit":10,"order":"","count_only":false,"label":"Description"}}]}}
2. If it's a general question or help, use: {{"type":"text","message":"your helpful response"}}
3. Always check Knowledge Articles (knowledge.article) for "how to" or informational questions.
4. Be friendly and professional.

SCHEMA:
{schema}

EXAMPLES:
Q: "Show me all contacts"
A: {{"type":"data","queries":[{{"model":"res.partner","domain":[],"fields":["name","email","phone"],"limit":20,"order":"name asc","count_only":false,"label":"All Contacts"}}]}}
"""

    # ═══════════════════════════════════════════════════════════════
    # Main public methods
    # ═══════════════════════════════════════════════════════════════

    # ── Prompt refiner — uses a fast model to clean up user queries ──
    REFINER_MODEL = 'llama-3.1-8b-instant'
    REFINER_PROMPT = """You are a prompt refiner for an Odoo 18 ERP AI Assistant that queries business data.

YOUR JOB: Take the user's raw question and rewrite it as a clear, precise business data question.

AVAILABLE DATA (the AI can ONLY query these):
- Contacts (res.partner): name, email, phone, city, company_name, country
- CRM Leads (crm.lead): name, contact_name, expected_revenue, stage (New/Qualified/Proposition/Won), create_date
- Sales Orders (sale.order): name, customer, amount_total, state (draft/sale/done), date_order
- Sales Order Lines (sale.order.line): order, product, quantity, price
- Employees (hr.employee): name, job_title, department, work_email, work_phone
- Departments (hr.department): name, manager
- Job Positions (hr.job): name, department, no_of_recruitment
- Products (product.template): name, list_price, type (service/consumable)
- Product Categories (product.category): name, parent
- Invoices (account.move): name, partner, amount_total, state, invoice_date
- Payments (account.payment): name, partner, amount, payment_type, date
- Stock Transfers (stock.picking): name, partner, state, scheduled_date
- Warehouses (stock.warehouse): name, code
- Stock on Hand (stock.quant): product, location, quantity
- Calendar Events (calendar.event): name, start, stop, user, location
- Sales Teams (crm.team): name, user
- CRM Stages (crm.stage): name, sequence
- Companies (res.company): name, email, phone, city
- Knowledge Base (knowledge.article): name, body

RULES — output ONLY the refined question, nothing else:

FIELD SYNONYMS (translate these):
- contact number/mobile number/cell → phone
- mail/email id/email address → email
- cost/rate/amount/price → list_price (products) or amount_total (sales/invoices)
- salary/pay → not available
- address/location → city
- designation/role → job_title
- boss/manager/head → manager_id or parent_id

SPECIFIC FIELD REQUESTS — keep them specific:
- "phone of X" → "What is the phone number of X?" (return ONLY name + phone)
- "email of X" → "What is the email of X?" (return ONLY name + email)
- "price of X" → "What is the price of X?" (return ONLY name + list_price)

SLANG → BUSINESS TERMS:
- paisa/money/cash → revenue or amount
- deal/deals → leads or sales orders
- bro/dude/yaar → remove
- stuff/things → products/contacts/leads
- closed/done → Won stage (leads) or confirmed (sales)
- stuck/pending → in New or Qualified stage
- big/huge → high revenue
- rn/atm → currently

SORTING/ORDERING — clarify the sort:
- "arrange/sort A-Z" → "Sort [items] alphabetically by name ascending"
- "sort high to low" → "Sort [items] by [field] descending"
- "rank by revenue" → "Show all [items] ranked by revenue descending"
- "top N / bottom N" → "Show top/bottom N [items] by [field]"

FILTERING — make filters explicit:
- "above/more than X" → "with [field] greater than X"
- "between X and Y" → "with [field] between X and Y"
- "from company X" → "where company is X"
- "this month/recent" → "created this month"
- "not in X" → "excluding [category] X"

GROUPING — specify what to group by:
- "by department" → "grouped by department"
- "by stage" → "grouped by stage"
- "by company" → "grouped by company name"

COUNTING/AGGREGATION — clarify the calculation:
- "total revenue" → "What is the sum of expected revenue from all CRM leads?"
- "average price" → "What is the average price of all products?"
- "how many X vs Y" → "How many [type X] and how many [type Y]?"

COMPARISON — make both sides clear:
- "X vs Y" → "Compare X and Y side by side showing [metric]"
- "difference between" → "What is the difference in [metric] between X and Y?"
- "which is higher" → "Compare [items] by [metric]"

DEDUPLICATION:
- "duplicates/repeated" → "Show all [items] sorted by name to identify duplicates"

RELATIONSHIPS:
- "contacts who work here" → "Show all employees with name, job title, and department"
- "who reports to X" → "Show employees in the department managed by X"
- "from company X" → "Show contacts where company name is X"

CALCULATIONS:
- "if all convert" → "What will be the total expected revenue if all [stage] leads convert to Won?"
- "if discount applied" → "Show all sales orders with their amounts for discount calculation"
- "percentage of X" → "What percentage of total [items] are [filtered condition]?"

CONDITIONAL:
- "if revenue > X mark as high" → "Show leads with expected revenue greater than X"
- "leads that need attention" → "Show leads in New or Qualified stage with high expected revenue"
UNIVERSAL KNOWLEDGE PATTERNS:
- Any informational question (how, what, why, procedure, guide, info) → Rewrite as a clear search for the main subject.
- References to "it", "this", "that article" → Replace with the actual subject discussed previously.
- Slang or complex sentences → Extract only the business/knowledge terms.

If the question is already clear, return it EXACTLY as-is."""

    def _refine_prompt(self, config, user_query):
        """Refine user's raw query into a clean business question using a fast model."""
        # Skip refinement for very short clear queries to save tokens
        q = user_query.lower().strip()
        simple_patterns = [
            'how many', 'list all', 'show all', 'show me all',
            'total number', 'what is the', 'who is',
        ]
        if any(q.startswith(p) for p in simple_patterns) and len(user_query.split()) <= 6:
            return user_query

        messages = [
            {"role": "system", "content": self.REFINER_PROMPT},
            {"role": "user", "content": user_query},
        ]

        # Use provider-specific model for refinement
        refiner_model = 'gpt-4o-mini' if config.provider == 'openai' else 'llama-3.1-8b-instant'

        refined = self._call_api_single(
            config, messages, temperature=0.0, max_tokens=150,
            json_mode=False, model=refiner_model,
        )

        if refined and refined != '__RATE_LIMITED__' and len(refined) > 3:
            refined = refined.strip('"\'').strip()
            # Don't use refinement if it's way longer than original (over-refinement)
            if len(refined) < len(user_query) * 5:
                _logger.info("Prompt refined: '%s' -> '%s'", user_query, refined)
                return refined

        # Fallback to original if refiner fails
        return user_query

    @api.model
    def generate_query(self, user_query, schema_json, conversation_history=None,
                       provider_override=None, model_override=None, pre_fetched_knowledge=""):
        """Send user query + schema to AI, get structured ORM queries back."""
        config = self.env['ai.config'].sudo().get_active_config()
        if not config:
            return {'type': 'error', 'message': 'AI Assistant not configured. Go to Configuration.'}

        provider_type = provider_override or config.provider
        api_key = config.openai_api_key if provider_type == 'openai' else config.groq_api_key
        
        if not api_key:
            return {'type': 'error', 'message': f'{provider_type.title()} API key not configured.'}

        # ── Step 1: Refine the prompt (SKIP if we already have knowledge context) ──
        if pre_fetched_knowledge:
            # If we already have articles, we don't need a refined search query
            refined_query = user_query
            full_query = user_query  # Keep user query clean, knowledge is in system prompt now
        else:
            refined_query = self._refine_prompt(config, user_query)
            full_query = refined_query

        today = date.today()
        try:
            odoo_version = json.loads(schema_json).get('odoo_version', '18.0') if schema_json else '18.0'
        except Exception:
            odoo_version = '18.0'

        # ── Step 2: Pick prompt based on model strength ──────────
        if provider_type == 'openai':
            selected_model = model_override or config.openai_model or "gpt-4o"
            is_smart = 'gpt-4' in selected_model or 'gpt-4o' in selected_model
            use_json_mode = True  # OpenAI models mostly support JSON mode
        else:
            selected_model = model_override or config.groq_model or "llama-3.3-70b-versatile"
            is_smart = selected_model in self.SMART_MODELS
            no_json = self.env['ai.config'].NO_JSON_MODE_MODELS
            use_json_mode = selected_model not in no_json

        format_args = {
            'odoo_version': odoo_version,
            'today': today.isoformat(),
            'first_of_month': today.replace(day=1).isoformat(),
            'schema': schema_json,
        }

        if is_smart:
            # Full complex prompt
            prompt_to_use = self.QUERY_SYSTEM_PROMPT
            if pre_fetched_knowledge:
                prompt_to_use += f"\n\nIMPORTANT CONTEXT (PRE-FETCHED KNOWLEDGE):\n{pre_fetched_knowledge}\n"
                prompt_to_use += "\nRULE: The answer is in the CONTEXT above. Do NOT generate a JSON data query. Respond ONLY with type 'text'."
            
            system_prompt = prompt_to_use.format(**format_args)
            max_tok = 512
        else:
            # Simplified prompt for all other models
            system_prompt = self.SIMPLE_QUERY_PROMPT.format(**format_args)
            max_tok = 512

        messages = [{"role": "system", "content": system_prompt}]

        # Add conversation history (last 2 exchanges — saves tokens)
        if conversation_history:
            history_limit = 4 if is_smart else 2  # less history for weaker models
            for msg in conversation_history[-history_limit:]:
                role = msg.get('role', 'user')
                content = msg.get('content', '')
                if role == 'assistant' and len(content) > 100:
                    content = content[:100] + '...'
                messages.append({"role": role, "content": content})

        # For models without json_mode, add extra JSON enforcement
        if not use_json_mode:
            messages.append({"role": "user", "content": f'Q: "{refined_query}"\nRespond with ONLY a JSON object. Start with {{ and end with }}.\nA:'})
        else:
            messages.append({"role": "user", "content": f'Q: "{refined_query}"\nA:'})

        response_text = self._call_api(
            config, messages, temperature=0.05, max_tokens=max_tok,
            json_mode=use_json_mode, model_override=model_override,
        )

        if not response_text:
            return None

        # Check for rate limit error
        if response_text.startswith('__RATE_LIMIT__:'):
            return {'type': 'error', 'message': response_text[15:]}

        parsed = self._parse_json_response(response_text)
        if parsed:
            _logger.info("AI query parsed: type=%s, queries=%d",
                         parsed.get('type'), len(parsed.get('queries', [])))
        else:
            _logger.warning("AI returned unparseable response: %s", response_text[:200])
        return parsed

    @api.model
    def generate_summary(self, user_query, table_results,
                         provider_override=None, model_override=None):
        """Generate a brief text summary of results."""
        config = self.env['ai.config'].sudo().get_active_config()
        if not config:
            return ""

        provider_type = provider_override or config.provider
        api_key = config.openai_api_key if provider_type == 'openai' else config.groq_api_key
        if not api_key:
            return ""

        parts = []
        for table in table_results:
            count = table.get('shown_count', 0)
            total = table.get('total_count', 0)
            label = table.get('label', '')
            rows = table.get('rows', [])
            headers = table.get('headers', [])
            field_keys = table.get('field_keys', [])

            if field_keys == ['count']:
                count_val = rows[0][0] if rows else '0'
                parts.append(f"{label}: {count_val}")
            elif count == 1 and rows:
                pairs = [f"{h}={v}" for h, v in zip(headers, rows[0]) if v and str(v).strip()]
                missing = [h for h, v in zip(headers, rows[0]) if not v or not str(v).strip()]
                parts.append(f"{label}: {', '.join(pairs[:4])}")
                if missing:
                    parts.append(f"[NOT AVAILABLE: {', '.join(missing)}]")
            elif count > 0 and rows:
                parts.append(f"{label}: {total} total records (showing {count})" if total > count else f"{label}: {total} records")
                for row in rows[:3]:
                    row_pairs = [f"{h}={v}" for h, v in zip(headers, row) if v]
                    parts.append(f"  - {', '.join(row_pairs[:3])}")
            else:
                parts.append(f"{label}: none found")

        context = "\n".join(parts) if parts else "No data found"

        query_lower = user_query.lower()
        is_business = any(w in query_lower for w in ['strategy', 'summary', 'situation', 'overview', 'should', 'would you', 'doing good', 'losing', 'focus'])
        is_calc = any(w in query_lower for w in ['total', 'sum', 'difference', 'gap', 'percentage', 'discount', 'convert', 'if all', 'if we'])

        if is_business:
            instruction = 'Write a business insight summary (3-5 sentences). Include specific numbers.'
        elif is_calc:
            instruction = 'Calculate the answer and state it clearly (1-2 sentences). Show the math.'
        else:
            instruction = 'Write a natural response (1-2 sentences). Include specific numbers. No metadata.'

        prompt = f'User asked: "{user_query}"\nData found:\n{context}\n\n{instruction}'
        messages = [
            {"role": "system", "content": "You write brief, accurate data summaries. Include actual numbers."},
            {"role": "user", "content": prompt},
        ]

        max_tok = 150 if is_business else (80 if is_calc else 50)
        return self._call_api(config, messages, temperature=0.3, max_tokens=max_tok,
                               model_override=model_override) or ""

    # ═══════════════════════════════════════════════════════════════
    # Step 3: Response Formatter — AI analyzes raw data for clean output
    # ═══════════════════════════════════════════════════════════════

    FORMATTER_PROMPT = """You are a helpful Odoo Expert Assistant. You are talking to a colleague.

RULES:
1. Be friendly, professional, and clear.
2. If no data was found, don't just say "No data". Say something like "I couldn't find any records for that, maybe try searching the Knowledge Base?" or "It seems we don't have that in the system yet."
3. For Knowledge Articles: Summarize the article content beautifully.
4. For Data: Present it clearly. Use bold for names and amounts.
5. If the user asked "how to", and you found an article, explain the steps clearly.
6. Avoid robotic language like "The query returned". Just talk naturally.
7. For calculations (sum/avg/diff): show the calculation and result.
8. For grouping: show the group breakdown with counts.
9. For comparisons: show both sides clearly with the conclusion.
10. Keep it concise — no filler words, no repeating the question.
11. Use bold (**text**) for key numbers and names.
12. If no records found, say "No data found" and suggest the user check the spelling or try a different query.
13. If a field is empty/missing (marked as [NOT AVAILABLE]), clearly tell the user that specific information is not available. Example: "The phone number for John is not available in the system."
14. Do NOT say "based on the data" or "the query returned" — just give the answer.
15. Do NOT show fields that are empty — only mention they are not available.
16. CRITICAL: When data says "TOTAL X records exist", always say "There are X [items]" using the TOTAL number, NOT the number of rows shown below. The shown rows are just a sample.
17. Do NOT include technical details like model names, field names, or domain filters."""

    @api.model
    def format_response(self, user_query, table_results, model_override=None):
        """
        Step 3: Take raw query results and format a user-friendly response.
        """
        config = self.env['ai.config'].sudo().get_active_config()
        if not config:
            return ""

        provider_type = config.provider
        api_key = config.openai_api_key if provider_type == 'openai' else config.groq_api_key
        if not api_key:
            return ""

        # Build data context from results
        data_parts = []
        for table in table_results:
            label = table.get('label', '')
            rows = table.get('rows', [])
            headers = table.get('headers', [])
            field_keys = table.get('field_keys', [])
            shown = table.get('shown_count', 0)
            total = table.get('total_count', 0)

            if field_keys == ['count']:
                count_val = rows[0][0] if rows else '0'
                data_parts.append(f"[COUNT] {label}: {count_val} (total in database: {total})")
            elif shown == 0:
                data_parts.append(f"[EMPTY] {label}: No records found")
            else:
                # For "top/bottom" queries, use shown count. For "all/show" queries, use total.
                q_low = user_query.lower()
                is_ranked = any(w in q_low for w in ['top ', 'bottom ', 'best ', 'worst ', 'highest', 'lowest'])
                if is_ranked:
                    data_parts.append(f"[DATA] {label}: {shown} records")
                elif total > shown:
                    data_parts.append(f"[DATA] {label}: TOTAL {total} records exist (showing first {shown} below)")
                else:
                    data_parts.append(f"[DATA] {label}: {total} records")
                # Send all rows to formatter when user wants full data
                q_low = user_query.lower()
                show_verbs = ['show ', 'list ', 'display ', 'give me ', 'show me ', 'show the ']
                wants_list = 'all' in q_low or any(q_low.startswith(v) for v in show_verbs)
                max_rows = 50 if wants_list else 10
                for row in rows[:max_rows]:
                    pairs = [f"{h}: {v}" for h, v in zip(headers, row) if v and str(v).strip()]
                    if pairs:
                        data_parts.append(f"  - {' | '.join(pairs)}")

        data_context = "\n".join(data_parts)

        prompt = (
            f'User asked: "{user_query}"\n\n'
            f'Raw data from database:\n{data_context}\n\n'
            f'Format a clean, user-friendly response following the rules. Only include relevant information.'
        )

        messages = [
            {"role": "system", "content": self.FORMATTER_PROMPT},
            {"role": "user", "content": prompt},
        ]

        # Determine token budget based on data size
        total_rows = sum(t.get('shown_count', 0) for t in table_results)
        if total_rows > 20:
            max_tok = 2048  # large list — need space to list all
        elif total_rows > 10:
            max_tok = 1024
        elif total_rows > 3:
            max_tok = 300
        else:
            max_tok = 150

        # Use provider-specific model for formatting
        formatter_model = 'gpt-4o-mini' if config.provider == 'openai' else 'llama-3.1-8b-instant'

        result = self._call_api_single(
            config, messages, temperature=0.3, max_tokens=max_tok,
            json_mode=False, model=formatter_model,
        )

        if result and result != '__RATE_LIMITED__':
            return result.strip()
        return ""

    def test_connection(self, config):
        """Test the connection to the AI provider."""
        provider_type = config.provider
        if provider_type == 'openai':
            api_key = config.openai_api_key
            model = config.openai_model
            url = "https://api.openai.com/v1/chat/completions"
        else:
            api_key = config.groq_api_key
            model = config.groq_model
            url = "https://api.groq.com/openai/v1/chat/completions"

        if not api_key:
            return False, "API Key is missing"

        try:
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": "Ping"}],
                "max_tokens": 5,
            }
            resp = requests.post(
                url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                timeout=10,
            )
            resp.raise_for_status()
            return True, f"Connection to {provider_type.title()} successful! Model {model} is responding."
        except Exception as e:
            error_msg = str(e)
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_msg = e.response.json().get('error', {}).get('message', error_msg)
                except:
                    pass
            return False, f"Connection failed: {error_msg}"

    # ═══════════════════════════════════════════════════════════════
    # API calls with auto-fallback
    # ═══════════════════════════════════════════════════════════════

    def _call_api(self, config, messages, temperature=0.0, max_tokens=256,
                   json_mode=False, model_override=None):
        """
        Call AI API with automatic model fallback.
        """
        provider_type = config.provider
        if provider_type == 'openai':
            primary_model = model_override or config.openai_model or "gpt-4o"
            # OpenAI doesn't need fallback list as much as Groq free tier
            models_to_try = [primary_model]
        else:
            primary_model = model_override or config.groq_model or "llama-3.3-70b-versatile"
            fallbacks = self.env['ai.config'].sudo().get_fallback_models(primary_model)
            models_to_try = [primary_model] + fallbacks

        for model in models_to_try:
            result = self._call_api_single(config, messages, temperature, max_tokens, json_mode, model)
            if result == '__RATE_LIMITED__':
                _logger.info("Model %s rate limited, trying next...", model)
                continue  # try next model
            return result  # success or non-rate-limit error

        # All models exhausted
        return "__RATE_LIMIT__:All models have reached their limit. Please try again later."

    def _call_api_single(self, config, messages, temperature, max_tokens, json_mode, model):
        """Call AI with a specific model. Returns '__RATE_LIMITED__' on 429."""
        provider_type = config.provider
        if provider_type == 'openai':
            api_key = config.openai_api_key
            url = "https://api.openai.com/v1/chat/completions"
        else:
            api_key = config.groq_api_key
            url = "https://api.groq.com/openai/v1/chat/completions"

        try:
            payload = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            # Handle JSON mode
            if json_mode:
                if provider_type == 'openai':
                    payload["response_format"] = {"type": "json_object"}
                else:
                    no_json = self.env['ai.config'].NO_JSON_MODE_MODELS
                    if model not in no_json:
                        payload["response_format"] = {"type": "json_object"}

            resp = requests.post(
                url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                timeout=config.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            text = data.get('choices', [{}])[0].get('message', {}).get('content', '').strip()
            _logger.info("%s OK (%d tokens, model: %s)",
                         provider_type.upper(),
                         data.get('usage', {}).get('total_tokens', 0), model)
            return text
        except requests.HTTPError as e:
            if e.response.status_code == 429:
                _logger.warning("%s rate limit on model: %s", provider_type.upper(), model)
                return '__RATE_LIMITED__'
            error_detail = ""
            try:
                error_detail = e.response.json().get('error', {}).get('message', '')
            except Exception:
                pass
            _logger.error("%s API error (%s): %s", provider_type.upper(), model, error_detail or e)
        except requests.ConnectionError:
            _logger.error("Cannot connect to %s API", provider_type.upper())
        except Exception as e:
            _logger.exception("%s error (%s): %s", provider_type.upper(), model, e)
        return None

    # ═══════════════════════════════════════════════════════════════
    # JSON parsing
    # ═══════════════════════════════════════════════════════════════

    def _parse_json_response(self, text):
        """Parse JSON from AI response with multiple fallback strategies."""
        text = text.strip()

        # Strip <think>...</think> tags (Qwen and reasoning models)
        text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()

        # Strategy 1: Direct parse
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            pass

        # Strategy 2: Strip markdown code fences
        cleaned = re.sub(r'^```(?:json)?\s*', '', text)
        cleaned = re.sub(r'\s*```$', '', cleaned).strip()
        try:
            return json.loads(cleaned)
        except (json.JSONDecodeError, ValueError):
            pass

        # Strategy 3: Extract first complete JSON object
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except (json.JSONDecodeError, ValueError):
                pass

        _logger.warning("Failed to parse AI JSON: %s", text[:300])
        return None
