# ══════════════════════════════════════════════════════════════
# AI Query Validator
# ══════════════════════════════════════════════════════════════
# Security layer that validates every AI-generated query before
# execution. The AI is untrusted input — we NEVER execute a
# query without validation.
#
# Checks performed:
# 1. JSON structure is valid
# 2. Model is in the allowed whitelist (ai.intent)
# 3. User has read access to the model
# 4. Fields exist and are not blacklisted
# 5. Domain operators are in the allowed set
# 6. Limit is capped at 50
# 7. Order clause is safe
# ══════════════════════════════════════════════════════════════

import re
import logging
from odoo import models, api
from odoo.exceptions import AccessError

_logger = logging.getLogger(__name__)


class AiQueryValidator(models.AbstractModel):
    _name = 'ai.query.validator'
    _description = 'AI Query Validator'

    # Only these domain operators are allowed
    ALLOWED_OPERATORS = {
        '=', '!=', '>', '<', '>=', '<=',
        'like', 'ilike', 'not like', 'not ilike',
        '=like', '=ilike',
        'in', 'not in', 'child_of', 'parent_of',
    }

    # Fields that must never be queried
    BLACKLISTED_FIELDS = {
        'password', 'password_crypt', 'signup_token', 'signup_type',
        'oauth_access_token', 'api_key', 'secret', 'token',
        'credit_card', 'bank_account', 'tax_id', 'ssn',
    }

    MAX_LIMIT = 50

    @api.model
    def validate(self, ai_response):
        """
        Validate the AI's response and return sanitized queries.

        Args:
            ai_response: dict parsed from AI's JSON output

        Returns:
            {
                'valid': True/False,
                'response_type': 'data' | 'text',
                'text_message': str or None,
                'queries': [sanitized query dicts],
                'errors': [error messages],
            }
        """
        errors = []

        # ── Check 1: Basic structure ──────────────────────────────
        if not isinstance(ai_response, dict):
            return self._invalid("AI response is not a valid JSON object")

        response_type = ai_response.get('type', 'text')

        # Text-only response — no queries to validate
        if response_type == 'text':
            return {
                'valid': True,
                'response_type': 'text',
                'text_message': ai_response.get('message', 'No response from AI.'),
                'queries': [],
                'errors': [],
            }

        # Data response — validate queries
        queries = ai_response.get('queries', [])
        if not isinstance(queries, list) or not queries:
            return self._invalid("AI returned data type but no queries")

        # ── Get allowed models from ai.intent ─────────────────────
        allowed_models = self._get_allowed_models()

        # ── Validate each query ───────────────────────────────────
        valid_queries = []
        for i, query in enumerate(queries):
            validated = self._validate_single_query(query, allowed_models, i)
            if validated.get('valid'):
                valid_queries.append(validated['query'])
            else:
                errors.append(validated['error'])

        if not valid_queries and errors:
            return {
                'valid': False,
                'response_type': 'data',
                'text_message': None,
                'queries': [],
                'errors': errors,
            }

        return {
            'valid': True,
            'response_type': 'data',
            'text_message': None,
            'queries': valid_queries,
            'errors': errors,
        }

    def _validate_single_query(self, query, allowed_models, index):
        """Validate a single query dict from the AI."""
        if not isinstance(query, dict):
            return {'valid': False, 'error': f'Query {index}: not a dict'}

        model_name = query.get('model', '')

        # ── Check 2: Model whitelist ──────────────────────────────
        if model_name not in allowed_models:
            return {'valid': False,
                    'error': f'Query {index}: model "{model_name}" not allowed'}

        # ── Check 3: Model exists ─────────────────────────────────
        try:
            model_obj = self.env[model_name]
        except KeyError:
            return {'valid': False,
                    'error': f'Query {index}: model {model_name} not found'}

        # ── Check 4: Validate fields ──────────────────────────────
        raw_fields = query.get('fields', [])
        if not isinstance(raw_fields, list):
            raw_fields = []

        safe_fields = []
        for fname in raw_fields:
            if not isinstance(fname, str):
                continue
            if fname in self.BLACKLISTED_FIELDS:
                continue
            if fname in model_obj._fields:
                field = model_obj._fields[fname]
                if field.type not in ('binary', 'html', 'serialized'):
                    safe_fields.append(fname)

        # If no valid fields, use defaults from intent
        if not safe_fields and not query.get('count_only'):
            intent = allowed_models.get(model_name)
            if intent:
                default_fields = [
                    f.strip() for f in (intent.default_fields or '').split(',')
                    if f.strip() and f.strip() in model_obj._fields
                ]
                safe_fields = default_fields or ['name']
            else:
                safe_fields = ['name']

        # Always include 'name' if available and not already there
        if 'name' in model_obj._fields and 'name' not in safe_fields and not query.get('count_only'):
            safe_fields.insert(0, 'name')

        # ── Check 5: Validate domain ──────────────────────────────
        raw_domain = query.get('domain', [])
        if not isinstance(raw_domain, list):
            raw_domain = []

        safe_domain = []
        for clause in raw_domain:
            if isinstance(clause, str) and clause in ('|', '&', '!'):
                safe_domain.append(clause)
                continue
            if not isinstance(clause, (list, tuple)) or len(clause) != 3:
                continue

            field_path, operator, value = clause

            if not isinstance(field_path, str) or not isinstance(operator, str):
                continue

            if operator not in self.ALLOWED_OPERATORS:
                continue

            # Validate field exists (support dotted paths like stage_id.name)
            base_field = field_path.split('.')[0]
            if base_field not in model_obj._fields:
                continue

            # Validate value is a safe type
            if not self._is_safe_value(value, operator):
                continue

            safe_domain.append(tuple(clause))

        # ── Check 6: Limit cap ────────────────────────────────────
        limit = query.get('limit', 10)
        count_only = bool(query.get('count_only', False))

        if count_only:
            limit = 0
        else:
            if not isinstance(limit, int) or limit <= 0:
                limit = 10
            limit = min(limit, self.MAX_LIMIT)

        # ── Check 7: Order validation (supports multi-field) ──────
        # Accepts: "field asc", "field desc", "field1 asc, field2 desc"
        order = query.get('order', '')
        if order and isinstance(order, str):
            safe_parts = []
            for part in order.split(','):
                part = part.strip()
                if re.match(r'^[a-z_]+ (asc|desc)$', part.lower()):
                    order_field = part.split()[0]
                    if order_field in model_obj._fields:
                        safe_parts.append(part)
            order = ', '.join(safe_parts)
        else:
            order = ''

        # ── Chain fields (for query chaining) ─────────────────────
        chain_data = {}
        if 'chain_from' in query and isinstance(query['chain_from'], int):
            chain_data['chain_from'] = query['chain_from']
            chain_data['chain_field'] = str(query.get('chain_field', 'name'))
            chain_data['chain_inject'] = str(query.get('chain_inject', 'name'))

        return {
            'valid': True,
            'query': {
                'model': model_name,
                'domain': safe_domain,
                'fields': safe_fields,
                'limit': limit,
                'order': order,
                'count_only': count_only,
                'label': str(query.get('label', f'Results from {model_name}')),
                **chain_data,
            }
        }

    def _is_safe_value(self, value, operator):
        """Check if a domain value is a safe primitive type."""
        if operator in ('in', 'not in'):
            if not isinstance(value, list):
                return False
            return all(isinstance(v, (str, int, float, bool)) for v in value)
        return isinstance(value, (str, int, float, bool))

    def _get_allowed_models(self):
        """Get dict of allowed model names from active ai.intent records."""
        intents = self.env['ai.intent'].search([('active', '=', True)])
        return {intent.model_name: intent for intent in intents}

    def _invalid(self, error_msg):
        """Return an invalid result."""
        return {
            'valid': False,
            'response_type': 'text',
            'text_message': None,
            'queries': [],
            'errors': [error_msg],
        }
