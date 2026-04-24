# ══════════════════════════════════════════════════════════════
# AI Schema Collector
# ══════════════════════════════════════════════════════════════
# Builds a compact representation of Odoo model schemas
# to send to the AI. The AI uses this to understand what
# tables/fields exist and generate valid ORM queries.
#
# Why compact? The AI model (qwen2.5:1.5b) has limited context.
# Every token counts, so we use short keys (n, t, l, r).
# ══════════════════════════════════════════════════════════════

import json
import logging
from datetime import date
from odoo import models, api, release

_logger = logging.getLogger(__name__)


class AiSchemaCollector(models.AbstractModel):
    _name = 'ai.schema.collector'
    _description = 'AI Schema Collector'

    # Fields to never expose in schema (security-sensitive)
    BLACKLISTED_FIELDS = {
        'password', 'password_crypt', 'signup_token', 'signup_type',
        'oauth_access_token', 'api_key', 'secret', 'token',
        'credit_card', 'bank_account', 'tax_id', 'ssn',
        '__last_update', 'write_uid', 'create_uid', 'write_date',
    }

    # Field types to skip (not useful for AI queries)
    SKIP_FIELD_TYPES = {'binary', 'html', 'serialized', 'properties',
                        'properties_definition', 'one2many', 'many2many'}

    # Max fields per model — keep compact to reduce tokens for cloud APIs
    MAX_FIELDS_PER_MODEL = 10

    @api.model
    def get_schema_for_ai(self):
        """
        Build compact JSON schema of all queryable Odoo models.
        Returns a JSON string ready to inject into the AI prompt.
        """
        intents = self.env['ai.intent'].search([('active', '=', True)])
        models_schema = []

        for intent in intents:
            model_name = intent.model_name
            try:
                model_obj = self.env[model_name]
            except KeyError:
                continue

            # Get prioritized fields from intent config
            priority_fields = [
                f.strip() for f in (intent.default_fields or '').split(',')
                if f.strip()
            ]

            fields_schema = self._build_fields_schema(model_obj, priority_fields)

            models_schema.append({
                'model': model_name,
                'label': intent.name,
                'desc': intent.description or intent.name,
                'fields': fields_schema,
            })

        schema = {
            'odoo_version': release.version,
            'today': date.today().isoformat(),
            'first_of_month': date.today().replace(day=1).isoformat(),
            'models': models_schema,
        }

        return json.dumps(schema, separators=(',', ':'))

    def _build_fields_schema(self, model_obj, priority_fields):
        """
        Build compact field list for a model.
        Priority fields come first, then we fill up to MAX_FIELDS_PER_MODEL.
        """
        fields_schema = []
        added_fields = set()

        # First: add priority fields (from intent default_fields)
        for fname in priority_fields:
            field_info = self._get_field_info(model_obj, fname)
            if field_info:
                fields_schema.append(field_info)
                added_fields.add(fname)

        # Always include these important fields if they exist
        always_include = ['name', 'create_date', 'active']
        for fname in always_include:
            if fname not in added_fields:
                field_info = self._get_field_info(model_obj, fname)
                if field_info:
                    fields_schema.append(field_info)
                    added_fields.add(fname)

        # Fill remaining slots with useful fields
        for fname, field in model_obj._fields.items():
            if len(fields_schema) >= self.MAX_FIELDS_PER_MODEL:
                break
            if fname in added_fields:
                continue
            if fname.startswith('_') or fname in self.BLACKLISTED_FIELDS:
                continue
            if field.type in self.SKIP_FIELD_TYPES:
                continue
            # Prefer queryable field types
            if field.type in ('char', 'text', 'integer', 'float', 'monetary',
                              'date', 'datetime', 'selection', 'boolean', 'many2one'):
                field_info = self._get_field_info(model_obj, fname)
                if field_info:
                    fields_schema.append(field_info)
                    added_fields.add(fname)

        return fields_schema

    def _get_field_info(self, model_obj, fname):
        """
        Get compact field info dict.
        Returns None if field should be skipped.
        Uses short keys to save tokens: n=name, t=type, l=label, r=relation, v=values
        """
        if fname not in model_obj._fields:
            return None

        field = model_obj._fields[fname]

        if fname in self.BLACKLISTED_FIELDS:
            return None
        if field.type in self.SKIP_FIELD_TYPES:
            return None

        info = {
            'n': fname,                          # field name
            't': field.type,                     # field type
            'l': field.string or fname,          # human label
        }

        # For many2one, include the related model name
        if field.type == 'many2one' and field.comodel_name:
            info['r'] = field.comodel_name

        # For selection fields, include possible values
        if field.type == 'selection':
            try:
                selection = field.selection
                if callable(selection):
                    selection = selection(model_obj)
                # Compact format: just value-label pairs
                info['v'] = [[s[0], s[1]] for s in selection[:10]]
            except Exception:
                pass

        return info
