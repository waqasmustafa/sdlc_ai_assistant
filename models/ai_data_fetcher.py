# ── ai_data_fetcher.py ───────────────────────────────────────────
# The "data layer" of the AI Assistant. After intent detection tells
# us *which* Odoo model to query, this module actually fetches the
# records via ORM, sanitises the fields, and formats everything into
# a text string that gets injected into the AI prompt.
#
# Key design decisions:
#   - Uses ORM (never raw SQL) so access rights are always enforced
#   - Blacklists sensitive fields (passwords, tokens, etc.)
#   - Caps results at 50 records to prevent memory issues
#   - Converts relational fields to display_name for readability

import logging
from odoo import models, api
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)


class AiDataFetcher(models.AbstractModel):
    """
    Data Fetching Layer.

    Responsible for:
    1. Querying Odoo models via ORM (inherits user's access rights)
    2. Converting records to structured JSON (only requested fields)
    3. Data minimization — never sends more than needed
    4. Formatting data as context string for the AI prompt

    SECURITY: All queries go through Odoo's ORM, which means:
    - Record rules are enforced (user only sees their allowed records)
    - Field access is checked
    - No raw SQL, ever
    """
    _name = 'ai.data.fetcher'
    _description = 'AI Data Fetcher'

    # Fields that should NEVER be sent to AI, even if requested
    BLACKLISTED_FIELDS = {
        'password', 'password_crypt', 'signup_token', 'signup_type',
        'oauth_access_token', 'api_key', 'secret', 'token',
        'credit_card', 'bank_account', 'tax_id', 'ssn',
        '__last_update', 'write_uid', 'create_uid',
    }

    # Maximum records to prevent memory issues
    ABSOLUTE_MAX_RECORDS = 50

    @api.model
    def fetch_data(self, intent_result):
        """
        Main entry point. Takes intent detection result, returns structured data.

        Args:
            intent_result: dict from ai.intent.detect_intent()

        Returns:
            {
                'success': True/False,
                'model': 'crm.lead',
                'record_count': 5,
                'total_count': 128,
                'fields': ['name', 'email', ...],
                'records': [{'name': 'John', 'email': '...'}, ...],
                'context_string': "formatted text for AI prompt",
                'error': None or error message,
            }
        """
        if not intent_result:
            return self._error_response("No intent detected")

        model_name = intent_result.get('model')
        field_list = intent_result.get('fields', [])
        domain = intent_result.get('domain', [])
        limit = min(
            intent_result.get('limit', 10),
            self.ABSOLUTE_MAX_RECORDS,
        )

        # Validate model exists and user has access
        try:
            model_obj = self.env[model_name]
        except KeyError:
            return self._error_response(f"Model '{model_name}' not found")

        # Check read access
        try:
            model_obj.check_access_rights('read', raise_exception=False)
        except AccessError:
            return self._error_response(
                f"You don't have permission to read {model_name} data"
            )

        # Sanitize fields — remove blacklisted, validate they exist
        safe_fields = self._sanitize_fields(model_obj, field_list)
        if not safe_fields:
            safe_fields = self._get_default_readable_fields(model_obj)

        # Execute query
        try:
            total_count = model_obj.search_count(domain)
            records = model_obj.search(domain, limit=limit, order='create_date desc')
            data = self._records_to_dicts(records, safe_fields)
        except AccessError as e:
            return self._error_response(f"Access denied: {e}")
        except Exception as e:
            _logger.exception("Error fetching data for model %s", model_name)
            return self._error_response(f"Error fetching data: {e}")

        context_string = self._format_context_string(
            model_name=model_name,
            intent_description=intent_result.get('description', ''),
            records=data,
            fields=safe_fields,
            total_count=total_count,
            fetched_count=len(data),
        )

        return {
            'success': True,
            'model': model_name,
            'record_count': len(data),
            'total_count': total_count,
            'fields': safe_fields,
            'records': data,
            'context_string': context_string,
            'error': None,
        }

    # ── Count-Only Fetch ──────────────────────────────────────────
    # For "how many?" questions — much faster than fetching full records.

    @api.model
    def fetch_count(self, intent_result):
        """
        Fetch only the count of records matching the domain.
        Used for "how many?" queries — no need to fetch full records.
        """
        model_name = intent_result.get('model')
        domain = intent_result.get('domain', [])

        try:
            model_obj = self.env[model_name]
            model_obj.check_access_rights('read', raise_exception=False)
            count = model_obj.search_count(domain)
        except Exception as e:
            return {'success': False, 'count': 0, 'error': str(e)}

        return {'success': True, 'count': count, 'model': model_name, 'error': None}

    # ── Aggregation Fetch ─────────────────────────────────────────
    # For questions like "highest revenue lead" or "average order total".
    # max/min → fetch the single top/bottom record, sorted by the field.
    # avg/sum → use read_group to compute the aggregate in the database.

    @api.model
    def fetch_aggregation(self, intent_result, agg_type, agg_field):
        """
        Fetch aggregated data (max, min, avg, sum).
        Used for "highest value lead", "average order amount", etc.
        """
        model_name = intent_result.get('model')
        domain = intent_result.get('domain', [])
        field_list = intent_result.get('fields', [])

        try:
            model_obj = self.env[model_name]
            model_obj.check_access_rights('read', raise_exception=False)
        except Exception as e:
            return {'success': False, 'error': str(e)}

        if agg_field not in model_obj._fields:
            return {'success': False, 'error': f"Field '{agg_field}' not found"}

        safe_fields = self._sanitize_fields(model_obj, field_list)
        if not safe_fields:
            safe_fields = self._get_default_readable_fields(model_obj)

        # Ensure agg_field is included
        if agg_field not in safe_fields:
            safe_fields.append(agg_field)

        try:
            if agg_type in ('max', 'min'):
                order_dir = 'desc' if agg_type == 'max' else 'asc'
                record = model_obj.search(domain, limit=1, order=f'{agg_field} {order_dir}')
                if not record:
                    return {'success': True, 'value': None, 'record': None, 'records': [],
                            'model': model_name, 'error': None}
                data = self._records_to_dicts(record, safe_fields)
                value = data[0].get(agg_field, 0) if data else 0
                return {
                    'success': True, 'value': value, 'record': data[0] if data else None,
                    'records': data, 'fields': safe_fields, 'model': model_name, 'error': None,
                }

            elif agg_type in ('avg', 'sum'):
                result = model_obj.read_group(domain, [agg_field], [])
                if result:
                    if agg_type == 'sum':
                        value = result[0].get(agg_field, 0)
                    else:  # avg
                        total = result[0].get(agg_field, 0)
                        count = result[0].get('__count', 1)
                        value = total / count if count else 0
                else:
                    value = 0
                return {
                    'success': True, 'value': value, 'record': None, 'records': [],
                    'fields': safe_fields, 'model': model_name, 'error': None,
                }

        except Exception as e:
            _logger.exception("Aggregation error for %s.%s", model_name, agg_field)
            return {'success': False, 'error': str(e)}

        return {'success': False, 'error': 'Unknown aggregation type'}

    # ═══════════════════════════════════════════════════════════════
    # NEW: Execute AI-generated validated queries → table results
    # ═══════════════════════════════════════════════════════════════

    @api.model
    def execute_validated_queries(self, validated_queries):
        """
        Execute a list of validated query dicts from the AI.
        Supports query chaining — results from one query can feed into the next.

        Uses the real user's permissions (not sudo) so Odoo access rights
        are enforced — users can only see data they're allowed to see.

        Chain format in query dict:
            'chain_from': 0          — index of previous query to chain from
            'chain_field': 'name'    — field from previous results to extract
            'chain_inject': 'company_name' — field in this query's domain to filter by

        Returns a list of table result dicts ready for the frontend.
        """
        # Use real user's env for data queries (respects access rights)
        data_fetch_uid = self.env.context.get('data_fetch_uid')
        if data_fetch_uid:
            fetcher = self.with_user(data_fetch_uid)
        else:
            fetcher = self

        table_results = []
        raw_results = []  # store raw record data for chaining

        for i, query in enumerate(validated_queries):
            try:
                # Handle query chaining — inject values from previous query
                if query.get('chain_from') is not None:
                    query = fetcher._apply_chain(query, raw_results)

                with fetcher.env.cr.savepoint():
                    table, records = fetcher._execute_single_query_with_records(query)
                    if table:
                        table_results.append(table)
                    raw_results.append(records or [])
            except AccessError:
                _logger.warning("Access denied for user on model %s", query.get('model'))
                table_results.append({
                    'label': query.get('label', 'Access Denied'),
                    'model': query.get('model', ''),
                    'headers': ['Info'],
                    'field_keys': ['info'],
                    'rows': [['You do not have permission to access this data.']],
                    'total_count': 0,
                    'shown_count': 0,
                })
                raw_results.append([])
            except Exception as e:
                _logger.exception("Error executing query for %s", query.get('model'))
                table_results.append({
                    'label': query.get('label', 'Error'),
                    'model': query.get('model', ''),
                    'headers': ['Info'],
                    'field_keys': ['info'],
                    'rows': [['Could not fetch data for this query.']],
                    'total_count': 0,
                    'shown_count': 0,
                })
                raw_results.append([])

        return table_results

    def _apply_chain(self, query, raw_results):
        """Inject values from a previous query result into this query's domain."""
        chain_idx = query.get('chain_from', 0)
        chain_field = query.get('chain_field', 'name')
        chain_inject = query.get('chain_inject', 'name')

        if chain_idx >= len(raw_results) or not raw_results[chain_idx]:
            return query

        # Extract values from previous result
        values = []
        for record in raw_results[chain_idx]:
            val = record.get(chain_field)
            if val and val not in values:
                values.append(val)

        if values:
            # Add filter to this query's domain
            query = dict(query)
            domain = list(query.get('domain', []))
            domain.append((chain_inject, 'in', values))
            query['domain'] = domain
            _logger.info("Chain applied: %s.%s from query %d (%d values)",
                         query.get('model'), chain_inject, chain_idx, len(values))

        return query

    def _execute_single_query_with_records(self, query):
        """Execute one validated query. Returns (table_dict, raw_records) for chaining."""
        return self._execute_single_query(query)

    def _execute_single_query(self, query):
        """Execute one validated query and return (table result dict, raw records)."""
        model_name = query['model']
        domain = query['domain']
        fields_list = query['fields']
        limit = query['limit']
        order = query['order']
        count_only = query.get('count_only', False)
        label = query.get('label', model_name)

        model_obj = self.env[model_name]

        # ── Count-only query ──────────────────────────────────────
        if count_only:
            count = model_obj.search_count(domain)
            return ({
                'label': label,
                'model': model_name,
                'headers': ['Count'],
                'field_keys': ['count'],
                'rows': [[str(count)]],
                'total_count': count,
                'shown_count': 1,
            }, [])

        # ── Regular data query ────────────────────────────────────
        total_count = model_obj.search_count(domain)
        records = model_obj.search(
            domain,
            limit=limit or 10,
            order=order or 'create_date desc',
        )

        if not records:
            return ({
                'label': label,
                'model': model_name,
                'headers': ['Info'],
                'field_keys': ['info'],
                'rows': [['No records found']],
                'total_count': 0,
                'shown_count': 0,
            }, [])

        # Convert records to flat row data
        data = self._records_to_dicts(records, fields_list)

        # Build headers from field labels
        headers = []
        for fname in fields_list:
            if fname in model_obj._fields:
                headers.append(model_obj._fields[fname].string or fname.replace('_', ' ').title())
            else:
                headers.append(fname.replace('_', ' ').title())

        # Build rows as flat lists of string values
        rows = []
        for record_dict in data:
            row = []
            for fname in fields_list:
                value = record_dict.get(fname, '')
                if isinstance(value, list):
                    value = ', '.join(str(v) for v in value)
                elif isinstance(value, float):
                    if value == int(value):
                        value = f"{int(value):,}"
                    else:
                        value = f"{value:,.2f}"
                elif value is None or value is False:
                    value = ''
                else:
                    value = str(value)
                row.append(value)
            rows.append(row)

        return ({
            'label': label,
            'model': model_name,
            'headers': headers,
            'field_keys': fields_list,
            'rows': rows,
            'total_count': total_count,
            'shown_count': len(rows),
        }, data)  # data = raw records for chaining

    # ── Private Helpers ──────────────────────────────────────────

    def _sanitize_fields(self, model_obj, field_list):
        """Remove blacklisted fields, validate field existence."""
        safe = []
        model_fields = model_obj._fields
        for fname in field_list:
            fname = fname.strip()
            if fname in self.BLACKLISTED_FIELDS:
                _logger.warning("Blocked blacklisted field: %s", fname)
                continue
            if fname not in model_fields:
                continue
            field = model_fields[fname]
            # Skip binary/attachment fields (no point sending to AI)
            if field.type in ('binary',):
                continue
            safe.append(fname)
        return safe

    def _get_default_readable_fields(self, model_obj):
        """Fallback: pick safe, human-readable fields."""
        candidates = []
        for fname, field in model_obj._fields.items():
            if fname.startswith('_') or fname in self.BLACKLISTED_FIELDS:
                continue
            if field.type in ('binary', 'html', 'serialized'):
                continue
            if fname in ('id', 'name', 'display_name'):
                candidates.append(fname)
                continue
            if field.type in ('char', 'text', 'integer', 'float', 'monetary',
                              'date', 'datetime', 'selection', 'boolean'):
                candidates.append(fname)
            if len(candidates) >= 8:
                break
        return candidates or ['name']

    def _records_to_dicts(self, records, fields):
        """
        Convert ORM recordset to list of plain dicts.
        Handles relational fields by using display_name.
        """
        result = []
        for rec in records:
            row = {}
            for fname in fields:
                try:
                    value = rec[fname]
                    field_type = rec._fields[fname].type

                    if field_type == 'many2one' and value:
                        row[fname] = value.display_name
                    elif field_type in ('one2many', 'many2many') and value:
                        row[fname] = [r.display_name for r in value[:5]]
                    elif field_type == 'datetime' and value:
                        row[fname] = value.strftime('%Y-%m-%d %H:%M')
                    elif field_type == 'date' and value:
                        row[fname] = value.strftime('%Y-%m-%d')
                    elif field_type == 'selection' and value:
                        # Get human-readable label
                        selection_list = rec._fields[fname].selection
                        if callable(selection_list):
                            selection_list = selection_list(rec)
                        label = dict(selection_list).get(value, value)
                        row[fname] = label
                    elif field_type == 'monetary' and value is not None:
                        row[fname] = float(value)
                    else:
                        row[fname] = value if value else ''

                except Exception:
                    row[fname] = ''
            result.append(row)
        return result

    def _format_context_string(self, model_name, intent_description,
                                records, fields, total_count, fetched_count):
        """
        Format fetched data into a structured context string for the AI prompt.
        This is what gets injected into the AI's context — NOT raw JSON.
        """
        lines = []
        lines.append(f"=== Odoo Data: {intent_description} ===")
        lines.append(f"Source: {model_name}")
        lines.append(f"Records shown: {fetched_count} of {total_count} total")
        lines.append(f"Fields: {', '.join(fields)}")
        lines.append("")

        if not records:
            lines.append("No records found matching the query.")
            return '\n'.join(lines)

        # Format as a readable table-like structure
        for i, record in enumerate(records, 1):
            lines.append(f"--- Record {i} ---")
            for fname in fields:
                value = record.get(fname, '')
                # Clean field name for display
                display_name = fname.replace('_', ' ').title()
                if isinstance(value, list):
                    value = ', '.join(str(v) for v in value)
                lines.append(f"  {display_name}: {value}")
            lines.append("")

        return '\n'.join(lines)

    def _error_response(self, message):
        """Return a standardized error response."""
        return {
            'success': False,
            'model': None,
            'record_count': 0,
            'total_count': 0,
            'fields': [],
            'records': [],
            'context_string': f"Error: {message}",
            'error': message,
        }
