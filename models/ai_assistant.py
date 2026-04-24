# ══════════════════════════════════════════════════════════════
# AI Assistant — Main Orchestrator
# ══════════════════════════════════════════════════════════════

import re
import json
import time
import logging
from datetime import date
from odoo import models, api

_logger = logging.getLogger(__name__)

# Maximum questions per user per day (0 = unlimited)
DAILY_QUESTION_LIMIT = 100


class AiAssistant(models.AbstractModel):
    _name = 'ai.assistant'
    _description = 'AI Assistant Orchestrator'

    @api.model
    def ask(self, query, conversation_id=None, model_override=None):
        """Main entry point. Takes a natural language query."""
        start_time = time.time()

        if not query or not query.strip():
            return self._text_response('Please enter a question.', None, start_time)

        query = query.strip()

        # ── Rate limit check: per-user daily limit ──────────────
        if DAILY_QUESTION_LIMIT > 0:
            user_id = self.env.context.get('data_fetch_uid') or self.env.uid
            today_start = date.today().isoformat() + ' 00:00:00'
            today_count = self.env['ai.message'].sudo().search_count([
                ('conversation_id.user_id', '=', user_id),
                ('role', '=', 'user'),
                ('create_date', '>=', today_start),
            ])
            if today_count >= DAILY_QUESTION_LIMIT:
                return self._text_response(
                    f"You've reached your daily limit of {DAILY_QUESTION_LIMIT} questions. "
                    f"Please try again tomorrow.",
                    None, start_time,
                )

        # ── Step 1: Reject greetings and small talk ───────────────
        greeting_patterns = r'^(hi|hello|hey|good morning|good evening|good afternoon|thanks|thank you|bye|goodbye|ok|okay|yes|no|hmm)\s*[!.?]*$'
        if re.match(greeting_patterns, query.lower().strip()):
            conversation = self._get_or_create_conversation(conversation_id)
            self.env['ai.message'].create({
                'conversation_id': conversation.id, 'role': 'user', 'content': query,
            })
            return self._text_response(
                self._build_guidance_message(),
                conversation, start_time,
            )

        # ── Step 2: Get or create conversation, save user message ─
        conversation = self._get_or_create_conversation(conversation_id)
        self.env['ai.message'].create({
            'conversation_id': conversation.id,
            'role': 'user',
            'content': query,
        })

        # ── Step 3: Build schema of all queryable models ──────────
        schema_json = self.env['ai.schema.collector'].get_schema_for_ai()

        # ── Step 4: Send schema + question to AI ──────────────────
        history = conversation.get_history_for_ai()
        clean_history = [
            msg for msg in history[:-1]
            if not (msg.get('role') == 'assistant' and any(
                err in (msg.get('content') or '')
                for err in ["couldn't process", "couldn't build", "Please try rephrasing",
                             "Rate limit", "rate limit", "API error"]
            ))
        ]
        # ── Step 3.5: Proactive Knowledge Base Search ──────────
        kb_context = ""
        try:
            # Search in name, body, and body_html
            kb_domain = [
                '|', '|',
                ('name', 'ilike', query),
                ('body', 'ilike', query),
                ('body_html', 'ilike', query)
            ]
            # sudo() to ensure internal users can read documentation regardless of access rules
            articles = self.env['knowledge.article'].sudo().search(kb_domain, limit=3)
            if articles:
                kb_context = "\n".join([
                    f"ARTICLE: {a.name}\nCONTENT: {a.body or a.body_html or ''}"
                    for a in articles
                ])
        except Exception as e:
            _logger.error(f"KB Search error: {e}")

        ai_response = self.env['ai.provider'].generate_query(
            user_query=query,
            schema_json=schema_json,
            conversation_history=clean_history,
            model_override=model_override,
            pre_fetched_knowledge=kb_context
        )

        if not ai_response:
            return self._text_response(
                "I couldn't process your question. Please try rephrasing it.",
                conversation, start_time,
            )

        # ── Step 4.5: Check for rate limit / API error ──────────
        if ai_response.get('type') == 'error':
            return self._text_response(
                ai_response.get('message', 'An error occurred with the AI provider.'),
                conversation, start_time,
            )

        # ── Step 5: If AI says it's a text response, return text ──
        if ai_response.get('type') == 'text':
            message = ai_response.get('message', self._build_guidance_message())
            return self._text_response(message, conversation, start_time)

        # ── Step 6: Validate AI-generated queries ─────────────────
        validated = self.env['ai.query.validator'].validate(ai_response)

        if not validated['valid'] or not validated['queries']:
            error_msg = '; '.join(validated.get('errors', []))
            _logger.warning("Query validation failed: %s", error_msg)
            return self._text_response(
                "I couldn't build a valid query for your question. Please try rephrasing it.",
                conversation, start_time,
            )

        # ── Step 6.5: Override limit when user wants full data ─────
        q_lower = query.lower()
        # Detect if user wants to see all/full data (not just a summary)
        show_verbs = ['show ', 'list ', 'display ', 'give me ', 'show me ', 'show the ', 'list the ']
        wants_full = (
            any(kw in q_lower for kw in [
                'show all', 'list all', 'show me all', 'give me all',
                'display all', 'all contacts', 'all leads', 'all employees',
                'all products', 'all sales', 'all orders', 'all invoices',
                'all departments', 'all payments', 'all transfers',
                'all meetings', 'all events', 'all teams', 'all stages',
                'all jobs', 'all warehouses', 'all categories', 'all companies',
            ])
            or any(q_lower.startswith(v) for v in show_verbs)
        )
        # But NOT for specific single-item queries
        single_item_words = ['highest', 'lowest', 'most', 'least', 'best', 'worst',
                             'top ', 'first', 'last', 'biggest', 'smallest',
                             'phone', 'email', 'price', 'address', 'number of']
        if wants_full and not any(w in q_lower for w in single_item_words):
            for vq in validated['queries']:
                if not vq.get('count_only'):
                    vq['limit'] = 50  # max allowed

        # ── Step 7: Execute validated queries on Odoo ORM ─────────
        table_results = self.env['ai.data.fetcher'].execute_validated_queries(
            validated['queries']
        )

        # ── Step 7.5: Separate count-only tables from data tables ─
        count_tables = []
        data_tables = []
        for t in table_results:
            if t.get('field_keys') == ['count']:
                count_tables.append(t)
            else:
                data_tables.append(t)

        elapsed = time.time() - start_time
        models_accessed = ', '.join(set(t.get('model', '') for t in table_results))
        total_records = sum(t.get('shown_count', 0) for t in table_results)

        # ── Step 8: RESPONSE FORMATTER (Step 3 of AI pipeline) ────
        # Send raw data back to AI for user-friendly formatting.
        # For simple cases (counts, single records) → build locally to save tokens.
        # For complex results → AI formats with insights.

        needs_ai_format = self._needs_ai_format(query, count_tables, data_tables)

        if needs_ai_format:
            formatted = self.env['ai.provider'].format_response(
                query, table_results, model_override=model_override,
            )
        else:
            formatted = self._build_smart_summary(query, count_tables, data_tables)

        # ── Step 9: Determine response type ───────────────────────
        if count_tables and not data_tables:
            # Count-only → text response, no table
            text_response = formatted or self._build_count_text(count_tables)
            self.env['ai.message'].create({
                'conversation_id': conversation.id,
                'role': 'assistant',
                'content': text_response,
                'model_accessed': models_accessed,
                'records_accessed': total_records,
                'response_time': elapsed,
                'ai_provider': conversation.env['ai.config'].sudo().get_active_config().provider or 'groq',
                'table_data': json.dumps(table_results),
            })
            return {
                'success': True,
                'response': text_response,
                'response_type': 'text',
                'tables': [],
                'conversation_id': conversation.id,
                'model_accessed': models_accessed,
                'records_found': total_records,
                'response_time': round(elapsed, 2),
                'error': '',
            }

        # ── Step 9.5: Decide if tables should be shown ────────────
        # If the user asked for a SUMMARY (total/average/count/percentage/
        # difference), they don't want the raw data table — just the answer.
        # Only show tables when user explicitly asks to LIST/SHOW/DISPLAY.
        show_tables = self._should_show_table(query)
        display_tables = (data_tables if data_tables else table_results) if show_tables else []
        text_response = formatted or self._build_text_from_tables(table_results)

        self.env['ai.message'].create({
            'conversation_id': conversation.id,
            'role': 'assistant',
            'content': text_response,
            'model_accessed': models_accessed,
            'records_accessed': total_records,
            'response_time': elapsed,
            'ai_provider': 'groq',
            'table_data': json.dumps(table_results),
        })

        return {
            'success': True,
            'response': text_response,
            'response_type': 'data' if display_tables else 'text',
            'tables': display_tables,
            'conversation_id': conversation.id,
            'model_accessed': models_accessed,
            'records_found': total_records,
            'response_time': round(elapsed, 2),
            'error': '',
        }

    # ═══════════════════════════════════════════════════════════════
    # Response builders
    # ═══════════════════════════════════════════════════════════════

    def _text_response(self, message, conversation, start_time):
        elapsed = time.time() - start_time
        if conversation:
            self.env['ai.message'].create({
                'conversation_id': conversation.id,
                'role': 'assistant',
                'content': message,
                'response_time': elapsed,
                'ai_provider': 'direct',
            })
        return {
            'success': True,
            'response': message,
            'response_type': 'text',
            'tables': [],
            'conversation_id': conversation.id if conversation else 0,
            'model_accessed': '',
            'records_found': 0,
            'response_time': round(elapsed, 2),
            'error': '',
        }

    def _build_guidance_message(self):
        intents = self.env['ai.intent'].search([('active', '=', True)])
        topics = [i.name for i in intents]
        return (
            f"Sorry, I can only assist with your business data.\n\n"
            f"I can help you with: **{', '.join(topics)}**.\n\n"
            f"Try asking something like:\n"
            f"- \"How many contacts do we have?\"\n"
            f"- \"Show me recent leads\"\n"
            f"- \"Top 5 sales orders\"\n"
            f"- \"Engineering team members\""
        )

    def _needs_ai_format(self, query, count_tables, data_tables):
        """Decide if we need AI Response Formatter or can build locally.
        AI formatting is used for complex results that need analysis."""
        q = query.lower()
        # Simple single counts → build locally
        if len(count_tables) == 1 and not data_tables:
            return False
        # Multiple counts (comparison) → use AI
        if len(count_tables) >= 2:
            return True
        # Business insight → use AI
        if any(w in q for w in ['strategy', 'summary', 'situation', 'overview',
                                 'should', 'would you', 'doing good', 'losing',
                                 'focus', 'predict', 'recommend']):
            return True
        # Calculations → use AI
        if any(w in q for w in ['difference', 'gap', 'percentage', 'compare', 'vs',
                                 'contribution', 'discount', 'convert', 'if all',
                                 'total revenue', 'average', 'sum', 'group']):
            return True
        # Multi-table results → use AI
        if len(data_tables) >= 2:
            return True
        # Large data set (>5 rows) → use AI for highlights
        if data_tables and any(t.get('shown_count', 0) > 5 for t in data_tables):
            return True
        # Single record lookup → build locally
        if data_tables and all(t.get('shown_count', 0) <= 1 for t in data_tables):
            return False
        # Small data (2-5 rows) → use AI for better formatting
        if data_tables:
            return True
        return False

    def _should_show_table(self, query):
        """Decide if the raw data table should be shown to the user.
        Returns True only when user explicitly asks to SEE a list of data.
        Summary/single-answer questions → text only, no table."""
        q = query.lower()

        # ── Step 1: Check summary keywords FIRST (these NEVER show table) ──
        summary_keywords = [
            'how many', 'total', 'count', 'sum', 'average', 'avg',
            'percentage', 'percent', 'difference', 'gap',
            'highest', 'lowest', 'maximum', 'minimum', 'max', 'min',
            'most', 'least', 'best', 'worst',
            'compare', 'vs', 'versus', 'comparison',
            'if all', 'if we', 'discount', 'convert',
            'what is the', 'what are the', 'which is the',
            'revenue of', 'price of', 'amount of',
            'how much', 'how is', 'are we', 'is there',
            'any', 'which has', 'who has', 'which department',
            'strategy', 'summary', 'overview', 'insight',
            'group by', 'grouped', 'per department', 'per stage',
            'duplicate', 'repeated',
        ]
        if any(kw in q for kw in summary_keywords):
            return False

        # ── Step 2: Check if user wants to SEE data ──
        show_verbs = ['show ', 'show me ', 'show the ', 'list ', 'display ',
                      'give me ', 'get me ', 'list the ']
        has_show = any(q.startswith(v) for v in show_verbs)
        has_all = any(kw in q for kw in [
            'all contacts', 'all leads', 'all employees', 'all products',
            'all sales', 'all orders', 'all invoices', 'all departments',
            'all payments', 'all transfers', 'all meetings', 'all events',
            'show all', 'list all', 'show me all', 'give me all',
        ])

        if has_show or has_all:
            # But NOT for single-item queries
            single_item = ['the highest', 'the lowest', 'the most', 'the least',
                           'the best', 'the worst', 'the top', 'the biggest',
                           'the smallest', 'the first', 'the last',
                           'phone', 'email', 'price', 'address', 'number of']
            if any(kw in q for kw in single_item):
                return False
            return True

        # Default: no table
        return False

    def _build_smart_summary(self, query, count_tables, data_tables):
        q = query.lower()
        parts = []

        # Handle count results
        for t in count_tables:
            label = t.get('label', 'Count')
            count_val = t['rows'][0][0] if t.get('rows') else '0'
            parts.append(f"{label}: **{count_val}**")

        # Handle data results with smart analysis
        for t in data_tables:
            shown = t.get('shown_count', 0)
            total = t.get('total_count', 0)
            label = t.get('label', '')
            rows = t.get('rows', [])
            headers = t.get('headers', [])

            if shown == 0:
                parts.append(f"No data found. The record may not exist or the requested information is not available.")
                continue

            # Single record — state values directly
            if shown == 1 and rows:
                pairs = []
                missing = []
                for h, v in zip(headers, rows[0]):
                    if v and str(v).strip():
                        pairs.append(f"**{h}**: {v}")
                    else:
                        missing.append(h)
                if pairs:
                    parts.append(', '.join(pairs))
                if missing:
                    missing_str = ', '.join(missing)
                    parts.append(f"{missing_str} is not available.")
                if not pairs and not missing:
                    parts.append("Record found but no data available.")
                continue

            # Check if user wants aggregation (sum/average/total)
            numeric_col = self._find_numeric_column(rows, headers)

            if numeric_col is not None and any(w in q for w in ['total', 'sum', 'all revenue', 'total revenue']):
                total_val = self._sum_column(rows, numeric_col)
                parts.append(f"**Total {headers[numeric_col]}**: {total_val:,.2f}")

            elif numeric_col is not None and any(w in q for w in ['average', 'avg', 'mean']):
                avg_val = self._avg_column(rows, numeric_col)
                parts.append(f"**Average {headers[numeric_col]}**: {avg_val:,.2f}")

            elif 'duplicate' in q or 'repeated' in q:
                dupes = self._find_duplicates(rows, 0)  # check first column (name)
                if dupes:
                    parts.append(f"Possible duplicates found: {', '.join(dupes)}")
                else:
                    parts.append("No duplicates found.")

            elif 'group' in q or 'by department' in q or 'by stage' in q:
                # Group by the last column (usually the grouping field)
                groups = self._group_by_column(rows, headers, -1)
                group_parts = [f"**{grp}**: {cnt}" for grp, cnt in groups.items()]
                parts.append(' | '.join(group_parts))

            else:
                # For "top/bottom" queries, use shown count. For others, use total.
                is_ranked = any(w in q for w in ['top ', 'bottom ', 'best ', 'worst ', 'highest', 'lowest'])
                if is_ranked:
                    parts.append(f"Here are the top **{shown}** results:")
                elif total > shown:
                    parts.append(f"There are **{total}** records in total (showing {shown}).")
                elif total > 0:
                    parts.append(f"There are **{total}** records.")
                else:
                    parts.append(f"Found **{shown}** records.")

        return ' '.join(parts) if parts else ''

    def _find_numeric_column(self, rows, headers):
        """Find the first numeric column in the data (for sum/avg)."""
        if not rows:
            return None
        for i, val in enumerate(rows[0]):
            try:
                cleaned = str(val).replace(',', '').replace('$', '').strip()
                if cleaned and float(cleaned):
                    return i
            except (ValueError, TypeError):
                continue
        return None

    def _sum_column(self, rows, col_idx):
        """Sum all values in a column."""
        total = 0
        for row in rows:
            try:
                total += float(str(row[col_idx]).replace(',', '').replace('$', ''))
            except (ValueError, TypeError, IndexError):
                pass
        return total

    def _avg_column(self, rows, col_idx):
        """Average all values in a column."""
        total = 0
        count = 0
        for row in rows:
            try:
                total += float(str(row[col_idx]).replace(',', '').replace('$', ''))
                count += 1
            except (ValueError, TypeError, IndexError):
                pass
        return total / count if count else 0

    def _find_duplicates(self, rows, col_idx):
        """Find duplicate values in a column."""
        seen = {}
        for row in rows:
            try:
                val = str(row[col_idx]).strip().lower()
                if val:
                    seen[val] = seen.get(val, 0) + 1
            except (IndexError, TypeError):
                pass
        return [name for name, count in seen.items() if count > 1]

    def _group_by_column(self, rows, headers, col_idx):
        """Group and count by a column."""
        groups = {}
        for row in rows:
            try:
                val = str(row[col_idx]).strip() or 'Unknown'
                groups[val] = groups.get(val, 0) + 1
            except (IndexError, TypeError):
                pass
        return dict(sorted(groups.items(), key=lambda x: x[1], reverse=True))

    def _build_count_text(self, count_tables):
        if not count_tables:
            return "No data found."
        parts = []
        for t in count_tables:
            label = t.get('label', 'Count')
            count_val = t['rows'][0][0] if t.get('rows') else '0'
            parts.append(f"{label}: **{count_val}**")
        return '\n'.join(parts)

    def _build_text_from_tables(self, table_results):
        if not table_results:
            return "No data found."
        lines = []
        for table in table_results:
            label = table.get('label', '')
            shown = table.get('shown_count', 0)
            total = table.get('total_count', 0)
            if table.get('field_keys') == ['count']:
                count_val = table['rows'][0][0] if table['rows'] else '0'
                lines.append(f"{label}: {count_val}")
            elif shown == 0:
                lines.append(f"{label}: No records found")
            else:
                lines.append(f"{label} ({shown} records):" if total <= shown else f"{label} (showing {shown} of {total}):")
                for row in table.get('rows', [])[:5]:
                    lines.append(f"  - {' | '.join(row[:4])}")
        return '\n'.join(lines)

    def _get_or_create_conversation(self, conversation_id):
        if conversation_id:
            conv = self.env['ai.conversation'].browse(conversation_id)
            if conv.exists() and conv.user_id == self.env.user:
                return conv
        return self.env['ai.conversation'].create({
            'user_id': self.env.user.id,
        })
