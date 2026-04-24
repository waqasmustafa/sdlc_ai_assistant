import re
import json
import logging
import requests
from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class AiIntent(models.Model):
    """
    Intent Detection Layer.
    Maps natural language queries to Odoo models + domains + fields.

    Two-stage detection:
    1. MODEL detection — which Odoo model is the user asking about?
       Uses keyword scoring against registered intents.
    2. QUERY TYPE detection — what kind of answer does the user want?
       count / search / field_lookup / aggregation / list / general
    """
    _name = 'ai.intent'
    _description = 'AI Intent Mapping'

    name = fields.Char(required=True)
    model_id = fields.Many2one('ir.model', required=True, ondelete='cascade')
    model_name = fields.Char(related='model_id.model', store=True)
    keywords = fields.Text(
        required=True,
        help='Comma-separated keywords/phrases that trigger this intent. '
             'Example: contact,contacts,customer,customers,partner,partners,client'
    )
    default_fields = fields.Text(
        required=True,
        help='Comma-separated field names to fetch by default. '
             'Example: name,email,phone,city,company_name'
    )
    default_limit = fields.Integer(default=10)
    description = fields.Text(
        help='Human-readable description of what this intent covers. '
             'Sent to AI as context so it understands the data.'
    )
    active = fields.Boolean(default=True)
    priority = fields.Integer(
        default=10,
        help='Higher priority intents win when multiple match equally.'
    )

    # ── Query type patterns ─────────────────────────────────────────
    # Detect WHAT the user wants (count, search, specific field, etc.)

    QUERY_TYPE_PATTERNS = [
        # Count: "how many contacts?", "total number of leads", "count of orders", "total staff?"
        ('count', r'\b(how many|count of|total number|number of|total\s+\w+\?)\b'),
        ('count', r'^total\s+\w+\s*\??\s*$'),
        # Field lookup: "email of Omkesh", "what is Rahul's phone", "phone number for Ahmed"
        ('field_lookup', r'\b(email|phone|mobile|address|job title|job|function|department|manager|salary|company|city)\s+(?:of|for)\s+'),
        ('field_lookup', r'\b(?:what is|what\'s|give me|get)\s+[\w\s]*(?:\'s|s\')\s+(email|phone|mobile|address|job|department|company|city)'),
        # Department/team list (BEFORE search, so "engineering team" isn't treated as name search)
        ('list', r'\b(?:engineering|sales|marketing|hr|human resources|finance)\s+(?:team|department|members|people|staff|group)\b'),
        # Search: "who is Rahul?", "find Omkesh", "search for Sarah", "Omkesh details"
        ('search', r'\b(?:who is|who\'s|find|search for|search|look up|look for|details of|details for|info on|info about|about|tell me about|why|where is)\s+'),
        ('search', r'\b([A-Z][a-z]+)\s+(?:details|info|information|profile|data|kaun|kon)\b'),
        # Single name query: just a capitalized name with ? (e.g., "Rahul?")
        ('search', r'^[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\s*\??\s*$'),
        # Aggregation: "highest value lead", "most expensive product", "total revenue"
        ('aggregation', r'\b(highest|lowest|most|least|biggest|smallest|top 1|best|worst|maximum|minimum|average|avg|sum of|total revenue|total amount|total sales)\b'),
        # List: "show all", "list", "display", "give me", "get", "from <place>" etc.
        ('list', r'\b(list|display|give me|get all|get me|fetch|all)\b'),
        ('list', r'\b(?:contacts|leads|employees|products|orders|invoices|sales|departments)\s+(?:from|in|with|added|created|this|last)\b'),
    ]

    # ── Natural language to ORM field name mapping ──────────────────
    FIELD_NAME_MAP = {
        'email': ['email', 'email_from', 'work_email'],
        'phone': ['phone', 'work_phone', 'mobile', 'phone_sanitized'],
        'mobile': ['mobile', 'phone', 'work_phone'],
        'address': ['street', 'city', 'country_id'],
        'city': ['city', 'work_location_name'],
        'job': ['function', 'job_title', 'job_id'],
        'job title': ['function', 'job_title', 'job_id'],
        'function': ['function', 'job_title'],
        'department': ['department_id'],
        'manager': ['parent_id', 'manager_id'],
        'salary': ['wage'],
        'company': ['company_name', 'company_id', 'parent_id'],
    }

    # ── Aggregation field mapping ───────────────────────────────────
    # Maps query terms to numeric fields for aggregation queries
    AGG_FIELD_KEYWORDS = {
        'revenue': ['expected_revenue', 'amount_total', 'amount_untaxed'],
        'amount': ['amount_total', 'amount_untaxed', 'expected_revenue'],
        'value': ['expected_revenue', 'amount_total', 'list_price'],
        'price': ['list_price', 'standard_price', 'amount_total'],
        'cost': ['standard_price', 'list_price'],
        'total': ['amount_total', 'amount_untaxed', 'expected_revenue'],
        'sales': ['amount_total', 'expected_revenue'],
    }

    # ── Filter patterns for domain building ─────────────────────────

    FILTER_PATTERNS = [
        (r'\b(?:with|having|from)\s+(\S+@\S+|\w+\.\w+)', '_filter_email_or_domain'),
        (r'\b(this|last|current)\s+(month|week|year)\b', '_filter_date_range'),
        (r'\b(today|yesterday)\b', '_filter_date_day'),
        (r'\b(?:new|recent|recently|newly)\s*(?:added|created|made)?\b', '_filter_recent'),
        (r'\b(?:from|in)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)', '_filter_location'),
        (r'\b(?:top|last|latest|recent|first)\s+(\d+)\b', '_filter_limit'),
        (r'\b(?:status|stage|state)\s+(?:is|=|:)\s+(\w+)\b', '_filter_state'),
        (r'\b(?:created|added|made)\s+(?:after|since|from)\s+([\d-]+)\b', '_filter_date_after'),
        (r'\b(?:created|added|made)\s+(?:this|last|current)\s+(month|week|year)\b', '_filter_date_range_alt'),
        (r'\b(?:amount|revenue|total|value)\s*(?:>|above|over|greater than)\s*(\d+)\b', '_filter_amount_gt'),
        # Department filter: "engineering team", "sales department"
        (r'\b(engineering|sales|marketing|hr|human resources|finance)\s+(?:team|department|members|people|staff)\b', '_filter_department'),
    ]

    # ═══════════════════════════════════════════════════════════════
    # Main entry point
    # ═══════════════════════════════════════════════════════════════

    # Minimum confidence threshold — below this, use AI fallback
    CONFIDENCE_THRESHOLD = 0.25

    @api.model
    def detect_intent(self, user_query):
        """
        Two-stage intent detection:
        Stage 1: Fast regex-based matching (instant, free)
        Stage 2: If confidence is low, use Ollama to interpret the query (smart, 1-3s)
        """
        # ── Stage 1: Regex-based detection ────────────────────────
        result = self._regex_detect(user_query)

        if result and result['confidence'] >= self.CONFIDENCE_THRESHOLD:
            _logger.info(
                "REGEX Intent: %s | type=%s | confidence=%.2f",
                result['intent'].name, result['query_type'], result['confidence']
            )
            return result

        # ── Stage 2: AI-powered fallback ──────────────────────────
        _logger.info("Low regex confidence (%.2f), trying AI fallback for: %s",
                      result['confidence'] if result else 0, user_query)

        ai_result = self._ai_detect_intent(user_query)
        if ai_result:
            _logger.info(
                "AI Intent: %s | type=%s | search=%s | field=%s",
                ai_result['intent'].name, ai_result['query_type'],
                ai_result.get('search_term'), ai_result.get('field_request')
            )
            return ai_result

        # If AI fallback also fails, return regex result (even if low confidence)
        if result:
            return result

        _logger.info("No intent matched for query: %s", user_query)
        return None

    def _regex_detect(self, user_query):
        """Stage 1: Fast regex-based intent detection."""
        query_lower = user_query.lower().strip()
        intents = self.search([('active', '=', True)])
        best_match = None
        best_score = 0

        for intent in intents:
            score = intent._calculate_match_score(query_lower)
            if score > best_score:
                best_score = score
                best_match = intent

        if not best_match or best_score == 0:
            return None

        # Parse filters from query
        domain = best_match._parse_filters(query_lower)
        limit = best_match._parse_limit(query_lower)
        field_list = [f.strip() for f in best_match.default_fields.split(',') if f.strip()]

        # Detect query type
        query_type, extra = best_match._classify_query_type(user_query)

        search_term = extra.get('search_term')
        field_request = extra.get('field_request')
        agg_type = extra.get('agg_type')
        agg_field = extra.get('agg_field')

        # For search queries, add name filter to domain
        if search_term and query_type in ('search', 'field_lookup'):
            domain.append(('name', 'ilike', search_term))

        # For field_lookup, ensure the requested field is in the field list
        if field_request and field_request not in field_list:
            field_list.insert(0, field_request)

        # If date filters are present, include the date field
        date_fields_in_domain = [
            d[0] for d in domain
            if isinstance(d, (list, tuple))
            and d[0] in ('create_date', 'date_order', 'date_open', 'date', 'invoice_date')
        ]
        for df in date_fields_in_domain:
            if df not in field_list:
                field_list.append(df)

        return {
            'intent': best_match,
            'model': best_match.model_name,
            'fields': field_list,
            'domain': domain,
            'limit': limit or best_match.default_limit,
            'query': user_query,
            'confidence': min(best_score / 10.0, 1.0),
            'description': best_match.description or best_match.name,
            'query_type': query_type,
            'search_term': search_term,
            'field_request': field_request,
            'agg_type': agg_type,
            'agg_field': agg_field,
        }

    # ═══════════════════════════════════════════════════════════════
    # Stage 2: AI-powered intent detection (Ollama fallback)
    # ═══════════════════════════════════════════════════════════════

    AI_INTENT_PROMPT = """You are an intent classifier for an Odoo ERP assistant.
Given a user's question, determine:
1. Which data area they're asking about
2. What type of query it is
3. Any specific name or field they're looking for

Available data areas:
{available_intents}

Query types: count, search, field_lookup, aggregation, list

Respond ONLY with valid JSON, no explanation:
{{
    "area": "contacts" or "leads" or "sales" or "employees" or "departments" or "products" or "invoices" or "unknown",
    "query_type": "count" or "search" or "field_lookup" or "aggregation" or "list" or "general",
    "search_term": "person/record name or null",
    "field_request": "email" or "phone" or "company" or "job" or "department" or "city" or "address" or null,
    "agg_type": "max" or "min" or "avg" or "sum" or null,
    "filter_location": "city or country name or null",
    "filter_date": "today" or "this_week" or "this_month" or "this_year" or null,
    "limit": number or null
}}

Examples:
"Rahul?" -> {{"area": "contacts", "query_type": "search", "search_term": "Rahul", "field_request": null, "agg_type": null, "filter_location": null, "filter_date": null, "limit": null}}
"kitne log hai?" -> {{"area": "contacts", "query_type": "count", "search_term": null, "field_request": null, "agg_type": null, "filter_location": null, "filter_date": null, "limit": null}}
"Omkesh ka email?" -> {{"area": "contacts", "query_type": "field_lookup", "search_term": "Omkesh", "field_request": "email", "agg_type": null, "filter_location": null, "filter_date": null, "limit": null}}
"sabse bada deal?" -> {{"area": "leads", "query_type": "aggregation", "search_term": null, "field_request": null, "agg_type": "max", "filter_location": null, "filter_date": null, "limit": null}}
"Pune wale contacts" -> {{"area": "contacts", "query_type": "list", "search_term": null, "field_request": null, "agg_type": null, "filter_location": "Pune", "filter_date": null, "limit": null}}
"latest 5 orders" -> {{"area": "sales", "query_type": "list", "search_term": null, "field_request": null, "agg_type": null, "filter_location": null, "filter_date": null, "limit": 5}}
"total staff?" -> {{"area": "employees", "query_type": "count", "search_term": null, "field_request": null, "agg_type": null, "filter_location": null, "filter_date": null, "limit": null}}
"engineering team members" -> {{"area": "employees", "query_type": "list", "search_term": null, "field_request": null, "agg_type": null, "filter_location": null, "filter_date": null, "limit": null}}

User question: "{user_query}"
JSON:"""

    # Map AI area names to intent keyword matches
    AREA_TO_INTENT_KEYWORDS = {
        'contacts': ['contact', 'partner', 'customer', 'people', 'person'],
        'leads': ['lead', 'opportunity', 'crm', 'deal', 'pipeline'],
        'sales': ['sale', 'order', 'quotation'],
        'employees': ['employee', 'staff', 'worker', 'team member'],
        'departments': ['department', 'division'],
        'products': ['product', 'item', 'inventory'],
        'invoices': ['invoice', 'bill', 'payment'],
    }

    @api.model
    def _ai_detect_intent(self, user_query):
        """Use Ollama to interpret the user's query when regex fails."""
        config = self.env['ai.config'].sudo().get_active_config()
        if not config or config.provider != 'ollama':
            return None

        # Build available intents description for the prompt
        intents = self.search([('active', '=', True)])
        intent_desc = '\n'.join([
            '- %s: %s (keywords: %s)' % (i.name, i.description or '', i.keywords[:50])
            for i in intents
        ])

        prompt = self.AI_INTENT_PROMPT.format(
            available_intents=intent_desc,
            user_query=user_query,
        )

        try:
            resp = requests.post(
                '%s/api/chat' % config.ollama_url,
                json={
                    'model': config.ollama_model,
                    'messages': [{'role': 'user', 'content': prompt}],
                    'stream': False,
                    'options': {'temperature': 0.0, 'num_predict': 256},
                },
                timeout=15,
            )
            resp.raise_for_status()
            response_text = resp.json().get('message', {}).get('content', '')

            # Parse JSON from response
            ai_data = self._parse_ai_response(response_text)
            if not ai_data:
                return None

            return self._build_intent_from_ai(ai_data, user_query, intents)

        except Exception as e:
            _logger.warning("AI intent detection failed: %s", e)
            return None

    def _parse_ai_response(self, text):
        """Extract JSON from AI response text."""
        text = text.strip()
        # Try to find JSON in the response
        try:
            # Direct JSON
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try to extract JSON from markdown code block
        match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        _logger.warning("Could not parse AI intent response: %s", text[:200])
        return None

    def _build_intent_from_ai(self, ai_data, user_query, intents):
        """Convert AI-parsed data into our standard intent result dict."""
        area = ai_data.get('area', 'unknown')
        if area == 'unknown':
            return None

        # Find matching intent by area
        best_intent = None
        keywords = self.AREA_TO_INTENT_KEYWORDS.get(area, [])
        for intent in intents:
            intent_kw = intent.keywords.lower()
            for kw in keywords:
                if kw in intent_kw:
                    best_intent = intent
                    break
            if best_intent:
                break

        if not best_intent:
            return None

        # Build field list
        field_list = [f.strip() for f in best_intent.default_fields.split(',') if f.strip()]

        # Build domain from AI filters
        domain = []

        # Location filter
        location = ai_data.get('filter_location')
        if location:
            model_obj = self.env[best_intent.model_name]
            # Check country first
            country = self.env['res.country'].search([('name', 'ilike', location)], limit=1)
            if country and 'country_id' in model_obj._fields:
                domain.append(('country_id', '=', country.id))
            elif 'city' in model_obj._fields:
                domain.append(('city', 'ilike', location))
            elif 'department_id' in model_obj._fields:
                # For employees, location might mean department
                dept = self.env['hr.department'].search([('name', 'ilike', location)], limit=1)
                if dept:
                    domain.append(('department_id', '=', dept.id))

        # Date filter
        date_filter = ai_data.get('filter_date')
        if date_filter:
            from datetime import date, timedelta
            today = date.today()
            date_field = best_intent._get_date_field() or 'create_date'

            if date_filter == 'today':
                domain.append((date_field, '>=', today.isoformat()))
            elif date_filter == 'this_week':
                start = today - timedelta(days=today.weekday())
                domain.append((date_field, '>=', start.isoformat()))
            elif date_filter == 'this_month':
                domain.append((date_field, '>=', today.replace(day=1).isoformat()))
            elif date_filter == 'this_year':
                domain.append((date_field, '>=', today.replace(month=1, day=1).isoformat()))

        # Search term
        search_term = ai_data.get('search_term')
        query_type = ai_data.get('query_type', 'general')

        if search_term and query_type in ('search', 'field_lookup'):
            domain.append(('name', 'ilike', search_term))

        # Field request
        field_request = ai_data.get('field_request')
        if field_request:
            field_request = best_intent._resolve_field_name(field_request)
            if field_request and field_request not in field_list:
                field_list.insert(0, field_request)

        # Aggregation
        agg_type = ai_data.get('agg_type')
        agg_field = None
        if agg_type:
            agg_info = best_intent._extract_aggregation(user_query)
            agg_field = agg_info.get('agg_field')

        # Limit
        limit = ai_data.get('limit') or best_intent.default_limit

        # Date fields in domain
        date_fields_in_domain = [
            d[0] for d in domain
            if isinstance(d, (list, tuple))
            and d[0] in ('create_date', 'date_order', 'date_open', 'date', 'invoice_date')
        ]
        for df in date_fields_in_domain:
            if df not in field_list:
                field_list.append(df)

        return {
            'intent': best_intent,
            'model': best_intent.model_name,
            'fields': field_list,
            'domain': domain,
            'limit': limit,
            'query': user_query,
            'confidence': 0.75,  # AI detection gets a fixed confidence
            'description': best_intent.description or best_intent.name,
            'query_type': query_type,
            'search_term': search_term,
            'field_request': field_request,
            'agg_type': agg_type,
            'agg_field': agg_field,
        }

    # ═══════════════════════════════════════════════════════════════
    # Query type classification
    # ═══════════════════════════════════════════════════════════════

    def _classify_query_type(self, user_query):
        """
        Classify what type of answer the user wants.
        Returns (query_type, extra_dict).
        """
        self.ensure_one()
        query_lower = user_query.lower().strip()

        for qtype, pattern in self.QUERY_TYPE_PATTERNS:
            if re.search(pattern, query_lower, re.IGNORECASE):
                extra = {}

                if qtype == 'count':
                    extra = {}  # No extra info needed for count

                elif qtype == 'search':
                    extra['search_term'] = self._extract_search_term(user_query)

                elif qtype == 'field_lookup':
                    fl = self._extract_field_lookup(user_query)
                    extra['search_term'] = fl.get('search_term')
                    extra['field_request'] = fl.get('field_name')

                elif qtype == 'aggregation':
                    agg = self._extract_aggregation(user_query)
                    extra['agg_type'] = agg.get('agg_type')
                    extra['agg_field'] = agg.get('agg_field')

                return qtype, extra

        return 'general', {}

    def _extract_search_term(self, query):
        """Extract a person/record name from the query."""
        patterns = [
            # "who is Rahul", "find Omkesh", "tell me about Sarah Johnson"
            r'(?:who is|who\'s|find|search for|search|look up|look for|details of|details for|info on|info about|about|tell me about|why|where is)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)',
            # "Omkesh details" / "Rahul info"
            r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+(?:details|info|information|profile|data|kaun|kon)',
            # Single name: "Rahul?" or just "Rahul"
            r'^([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s*\??\s*$',
            # Fallback: any word after search keywords
            r'(?:who is|who\'s|find|search for|search|look up|look for|details of|details for|info on|info about|about|tell me about|why|where is)\s+(\w+(?:\s+\w+)?)',
        ]
        for pattern in patterns:
            match = re.search(pattern, query, re.IGNORECASE)
            if match:
                term = match.group(1).strip()
                # Filter out common non-name words
                skip_words = {'the', 'a', 'an', 'all', 'my', 'our', 'their', 'some',
                              'latest', 'recent', 'new', 'old', 'top', 'best'}
                if term.lower() not in skip_words:
                    return term
        return None

    def _extract_field_lookup(self, query):
        """
        Extract field name and person name from field lookup queries.
        E.g., "email of Omkesh" → {'field_name': 'email', 'search_term': 'Omkesh'}
        """
        result = {'field_name': None, 'search_term': None}

        # Pattern 1: "email of Omkesh" / "phone for Ahmed"
        match = re.search(
            r'\b(email|phone|mobile|address|job title|job|function|department|manager|salary|company|city)\s+(?:of|for)\s+(.+?)(?:\?|$)',
            query, re.IGNORECASE
        )
        if match:
            result['search_term'] = match.group(2).strip()
            result['field_name'] = self._resolve_field_name(match.group(1).lower().strip())
            return result

        # Pattern 2: "what is Rahul's email" / "what's Omkesh's phone"
        match = re.search(
            r'(?:what is|what\'s|give me|get)\s+(.+?)(?:\'s|s\')\s+(email|phone|mobile|address|job|department|company|city)',
            query, re.IGNORECASE
        )
        if match:
            result['search_term'] = match.group(1).strip()
            result['field_name'] = self._resolve_field_name(match.group(2).lower().strip())
            return result

        return result

    def _resolve_field_name(self, natural_name):
        """Map natural language field name to actual ORM field name."""
        candidates = self.FIELD_NAME_MAP.get(natural_name, [])
        if not candidates:
            return None

        model_obj = self.env[self.model_name]
        for field_name in candidates:
            if field_name in model_obj._fields:
                return field_name
        return None

    def _extract_aggregation(self, query):
        """
        Extract aggregation type and target field.
        E.g., "highest value lead" → {'agg_type': 'max', 'agg_field': 'expected_revenue'}
        """
        query_lower = query.lower()
        result = {'agg_type': None, 'agg_field': None}

        # Determine aggregation type
        if re.search(r'\b(highest|biggest|most|maximum|top 1|best|largest)\b', query_lower):
            result['agg_type'] = 'max'
        elif re.search(r'\b(lowest|smallest|least|minimum|worst|cheapest)\b', query_lower):
            result['agg_type'] = 'min'
        elif re.search(r'\b(average|avg|mean)\b', query_lower):
            result['agg_type'] = 'avg'
        elif re.search(r'\b(sum of|total revenue|total amount|total sales)\b', query_lower):
            result['agg_type'] = 'sum'

        # Determine which field to aggregate on
        model_obj = self.env[self.model_name]
        for keyword, field_candidates in self.AGG_FIELD_KEYWORDS.items():
            if keyword in query_lower:
                for fname in field_candidates:
                    if fname in model_obj._fields:
                        result['agg_field'] = fname
                        return result

        # Fallback: find the first monetary/float field on the model
        for fname, field in model_obj._fields.items():
            if field.type in ('monetary', 'float') and fname not in ('id', 'sequence', 'priority'):
                result['agg_field'] = fname
                break

        return result

    # ═══════════════════════════════════════════════════════════════
    # Scoring
    # ═══════════════════════════════════════════════════════════════

    def _calculate_match_score(self, query_lower):
        """Score how well this intent matches the query."""
        self.ensure_one()
        keywords = [k.strip().lower() for k in self.keywords.split(',') if k.strip()]
        score = 0
        for keyword in keywords:
            if keyword in query_lower:
                score += len(keyword.split())
                pattern = r'\b' + re.escape(keyword) + r'\b'
                if re.search(pattern, query_lower):
                    score += 2
        score += self.priority / 100.0
        return score

    # ═══════════════════════════════════════════════════════════════
    # Filter parsing
    # ═══════════════════════════════════════════════════════════════

    def _parse_filters(self, query_lower):
        """Extract ORM domain filters from natural language."""
        self.ensure_one()
        domain = []
        for pattern, method_name in self.FILTER_PATTERNS:
            match = re.search(pattern, query_lower, re.IGNORECASE)
            if match:
                method = getattr(self, method_name, None)
                if method:
                    filter_domain = method(match, query_lower)
                    if filter_domain:
                        domain.extend(filter_domain)
        return domain

    def _parse_limit(self, query_lower):
        """Extract record limit from query like 'top 5' or 'last 20'."""
        match = re.search(r'\b(?:top|last|latest|recent|first)\s+(\d+)\b', query_lower)
        if match:
            limit = int(match.group(1))
            return min(limit, 50)
        return None

    # ── Filter methods ─────────────────────────────────────────────

    def _filter_date_day(self, match, query):
        """Parse 'today' / 'yesterday' into date domain."""
        from datetime import date, timedelta
        word = match.group(1).lower()
        today = date.today()

        if word == 'today':
            start = today
            end = today
        else:
            start = today - timedelta(days=1)
            end = today - timedelta(days=1)

        date_field = self._get_date_field()
        if not date_field:
            return []
        return [
            (date_field, '>=', start.isoformat()),
            (date_field, '<=', end.isoformat() + ' 23:59:59'),
        ]

    def _filter_recent(self, match, query):
        """Parse 'new' / 'recently added' — default to last 30 days."""
        from datetime import date, timedelta
        if re.search(r'\b(this|last|current)\s+(month|week|year)\b', query):
            return []
        if re.search(r'\b(today|yesterday)\b', query):
            return []
        start = date.today() - timedelta(days=30)
        date_field = self._get_date_field()
        if not date_field:
            return []
        return [(date_field, '>=', start.isoformat())]

    def _filter_date_range_alt(self, match, query):
        """Parse 'added this month' / 'created last week'."""
        from datetime import date, timedelta
        period = match.group(1).lower()
        mod_match = re.search(r'(this|last|current)\s+' + re.escape(period), query.lower())
        modifier = mod_match.group(1) if mod_match else 'this'
        today = date.today()

        if period == 'month':
            if modifier in ('this', 'current'):
                start = today.replace(day=1)
                end = today
            else:
                first_this_month = today.replace(day=1)
                end = first_this_month - timedelta(days=1)
                start = end.replace(day=1)
        elif period == 'week':
            if modifier in ('this', 'current'):
                start = today - timedelta(days=today.weekday())
                end = today
            else:
                start = today - timedelta(days=today.weekday() + 7)
                end = start + timedelta(days=6)
        elif period == 'year':
            if modifier in ('this', 'current'):
                start = today.replace(month=1, day=1)
                end = today
            else:
                start = today.replace(year=today.year - 1, month=1, day=1)
                end = today.replace(year=today.year - 1, month=12, day=31)
        else:
            return []

        date_field = self._get_date_field()
        if not date_field:
            return []
        return [
            (date_field, '>=', start.isoformat()),
            (date_field, '<=', end.isoformat()),
        ]

    def _filter_email_or_domain(self, match, query):
        """Parse 'with gmail' → [('email', 'ilike', 'gmail')]"""
        value = match.group(1)
        model_obj = self.env[self.model_name]
        if 'email' in model_obj._fields:
            return [('email', 'ilike', value)]
        if 'email_from' in model_obj._fields:
            return [('email_from', 'ilike', value)]
        return []

    def _filter_date_range(self, match, query):
        """Parse 'this month' / 'last week' into date domain."""
        from datetime import date, timedelta
        modifier = match.group(1).lower()
        period = match.group(2).lower()
        today = date.today()

        if period == 'month':
            if modifier in ('this', 'current'):
                start = today.replace(day=1)
                end = today
            else:
                first_this_month = today.replace(day=1)
                end = first_this_month - timedelta(days=1)
                start = end.replace(day=1)
        elif period == 'week':
            if modifier in ('this', 'current'):
                start = today - timedelta(days=today.weekday())
                end = today
            else:
                start = today - timedelta(days=today.weekday() + 7)
                end = start + timedelta(days=6)
        elif period == 'year':
            if modifier in ('this', 'current'):
                start = today.replace(month=1, day=1)
                end = today
            else:
                start = today.replace(year=today.year - 1, month=1, day=1)
                end = today.replace(year=today.year - 1, month=12, day=31)
        else:
            return []

        date_field = self._get_date_field()
        if not date_field:
            return []
        return [
            (date_field, '>=', start.isoformat()),
            (date_field, '<=', end.isoformat()),
        ]

    def _filter_location(self, match, query):
        """Parse 'from New York' → city or country filter."""
        location = match.group(1)
        model_obj = self.env[self.model_name]

        # Check if it's a country name
        country = self.env['res.country'].search(
            [('name', 'ilike', location)], limit=1
        )
        if country and 'country_id' in model_obj._fields:
            return [('country_id', '=', country.id)]

        # Otherwise treat as city
        if 'city' in model_obj._fields:
            return [('city', 'ilike', location)]
        if 'work_location_name' in model_obj._fields:
            return [('work_location_name', 'ilike', location)]
        return []

    def _filter_limit(self, match, query):
        return []

    def _filter_state(self, match, query):
        """Parse 'status is won' → domain filter."""
        state_value = match.group(1)
        model_obj = self.env[self.model_name]
        if 'stage_id' in model_obj._fields:
            return [('stage_id.name', 'ilike', state_value)]
        if 'state' in model_obj._fields:
            return [('state', '=', state_value)]
        return []

    def _filter_date_after(self, match, query):
        """Parse 'created after 2024-01-01'."""
        date_str = match.group(1)
        date_field = self._get_date_field()
        if date_field:
            return [(date_field, '>=', date_str)]
        return []

    def _filter_amount_gt(self, match, query):
        """Parse 'amount > 1000'."""
        amount = float(match.group(1))
        model_obj = self.env[self.model_name]
        for field_name in ('expected_revenue', 'amount_total', 'amount_untaxed', 'amount'):
            if field_name in model_obj._fields:
                return [(field_name, '>', amount)]
        return []

    def _filter_department(self, match, query):
        """Parse 'engineering team' into department filter."""
        dept_name = match.group(1)
        # Map short names to full names
        dept_map = {'hr': 'Human Resources'}
        dept_name = dept_map.get(dept_name.lower(), dept_name)

        model_obj = self.env[self.model_name]
        if 'department_id' in model_obj._fields:
            dept = self.env['hr.department'].search(
                [('name', 'ilike', dept_name)], limit=1
            )
            if dept:
                return [('department_id', '=', dept.id)]
        return []

    def _get_date_field(self):
        """Find the best date field for this intent's model."""
        model_obj = self.env[self.model_name]
        for field_name in ('create_date', 'date_order', 'date_open', 'date'):
            if field_name in model_obj._fields:
                return field_name
        return None
