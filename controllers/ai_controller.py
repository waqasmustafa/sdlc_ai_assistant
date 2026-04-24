# ══════════════════════════════════════════════════════════════
# AI Controller — HTTP endpoints for the frontend
# ══════════════════════════════════════════════════════════════

import io
import csv
import json
import logging
from odoo import http
from odoo.http import request
from odoo.exceptions import AccessError

_logger = logging.getLogger(__name__)


class AiAssistantController(http.Controller):

    def _check_ai_access(self):
        """Ensure current user has AI Assistant access."""
        if not request.env.user.has_group('sdlc_ai_assistant.group_ai_user'):
            raise AccessError("You do not have access to the AI Assistant.")

    @http.route('/ai_assistant/models', type='json', auth='user', methods=['POST'])
    def get_models(self):
        """Return available Groq model options for the dropdown."""
        try:
            self._check_ai_access()
            return request.env['ai.config'].sudo().get_available_models()
        except AccessError:
            return []
        except Exception as e:
            _logger.exception("Error loading models")
            return []

    @http.route('/ai_assistant/ask', type='json', auth='user', methods=['POST'])
    def ask(self, query, conversation_id=None, model=None):
        """Main chat endpoint."""
        try:
            self._check_ai_access()
            _logger.info("=== ASK: query=%s, model=%s ===", query, model)
            assistant = request.env['ai.assistant'].sudo()
            result = assistant.with_context(
                data_fetch_uid=request.env.uid,
            ).ask(
                query=query,
                conversation_id=conversation_id,
                model_override=model,
            )
            return {
                'success': result.get('success', False),
                'response': result.get('response', '') or '',
                'response_type': result.get('response_type', 'text'),
                'tables': result.get('tables', []) or [],
                'conversation_id': result.get('conversation_id'),
                'model_accessed': result.get('model_accessed', '') or '',
                'records_found': result.get('records_found', 0),
                'response_time': result.get('response_time', 0),
                'error': result.get('error') or '',
            }
        except Exception as e:
            _logger.exception("AI Assistant error")
            request.env.cr.rollback()
            return {
                'success': False,
                'response': 'An error occurred. Please try again.',
                'response_type': 'text',
                'tables': [],
                'conversation_id': conversation_id,
                'model_accessed': '',
                'records_found': 0,
                'response_time': 0,
                'error': str(e),
            }

    @http.route('/ai_assistant/conversations', type='json', auth='user', methods=['POST'])
    def list_conversations(self, limit=20):
        """List current user's conversations."""
        try:
            self._check_ai_access()
            conversations = request.env['ai.conversation'].sudo().search(
                [('user_id', '=', request.env.user.id)],
                limit=limit,
                order='create_date desc',
            )
            return [{
                'id': conv.id,
                'name': conv.name or 'New Chat',
                'message_count': conv.message_count,
                'create_date': conv.create_date.strftime('%Y-%m-%d %H:%M') if conv.create_date else '',
            } for conv in conversations]
        except Exception as e:
            _logger.exception("Error loading conversations")
            return []

    @http.route('/ai_assistant/conversation/delete', type='json', auth='user', methods=['POST'])
    def delete_conversation(self, conversation_id):
        """Delete a conversation (only own conversations)."""
        try:
            self._check_ai_access()
            conversation = request.env['ai.conversation'].sudo().browse(conversation_id)
            if not conversation.exists() or conversation.user_id != request.env.user:
                return {'success': False, 'error': 'Conversation not found'}
            conversation.message_ids.unlink()
            conversation.unlink()
            return {'success': True}
        except Exception as e:
            _logger.exception("Error deleting conversation")
            return {'success': False, 'error': str(e)}

    @http.route('/ai_assistant/conversation/rename', type='json', auth='user', methods=['POST'])
    def rename_conversation(self, conversation_id, name):
        """Rename a conversation (only own conversations)."""
        try:
            self._check_ai_access()
            conversation = request.env['ai.conversation'].sudo().browse(conversation_id)
            if not conversation.exists() or conversation.user_id != request.env.user:
                return {'success': False, 'error': 'Conversation not found'}
            name = (name or '').strip()
            if not name:
                return {'success': False, 'error': 'Name cannot be empty'}
            conversation.custom_name = name[:100]
            return {'success': True}
        except Exception as e:
            _logger.exception("Error renaming conversation")
            return {'success': False, 'error': str(e)}

    @http.route('/ai_assistant/export/csv', type='http', auth='user', methods=['GET'])
    def export_csv(self, headers='', rows='', label='Export'):
        """Export table data as CSV file."""
        try:
            headers_list = json.loads(headers)
            rows_list = json.loads(rows)
        except (json.JSONDecodeError, TypeError):
            return request.make_response('Invalid data', headers=[('Content-Type', 'text/plain')])

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(headers_list)
        for row in rows_list:
            writer.writerow(row)

        csv_content = output.getvalue()
        filename = label.replace(' ', '_').replace('/', '_') + '.csv'

        return request.make_response(
            csv_content,
            headers=[
                ('Content-Type', 'text/csv; charset=utf-8'),
                ('Content-Disposition', f'attachment; filename="{filename}"'),
            ],
        )

    @http.route('/ai_assistant/export/json', type='http', auth='user', methods=['GET'])
    def export_json(self, headers='', rows='', label='Export'):
        """Export table data as JSON file."""
        try:
            headers_list = json.loads(headers)
            rows_list = json.loads(rows)
        except (json.JSONDecodeError, TypeError):
            return request.make_response('Invalid data', headers=[('Content-Type', 'text/plain')])

        # Convert rows to list of dicts using headers as keys
        data = []
        for row in rows_list:
            record = {}
            for i, header in enumerate(headers_list):
                record[header] = row[i] if i < len(row) else ''
            data.append(record)

        json_content = json.dumps({'label': label, 'records': data}, indent=2, ensure_ascii=False)
        filename = label.replace(' ', '_').replace('/', '_') + '.json'

        return request.make_response(
            json_content,
            headers=[
                ('Content-Type', 'application/json; charset=utf-8'),
                ('Content-Disposition', f'attachment; filename="{filename}"'),
            ],
        )

    @http.route('/ai_assistant/conversation/messages', type='json', auth='user', methods=['POST'])
    def get_messages(self, conversation_id, offset=0, limit=50):
        """Get messages for a specific conversation with pagination."""
        try:
            self._check_ai_access()
            conversation = request.env['ai.conversation'].sudo().browse(conversation_id)
            if not conversation.exists() or conversation.user_id != request.env.user:
                return {'messages': [], 'total': 0, 'has_more': False}

            total = len(conversation.message_ids)
            messages = conversation.message_ids.sorted('create_date')

            # If offset > 0, we're loading older messages
            if offset > 0:
                messages = messages[:max(0, total - offset)]
            # Take the last `limit` messages (most recent)
            if len(messages) > limit:
                messages = messages[-limit:]
                has_more = True
            else:
                has_more = offset + len(messages) < total

            result = []
            for msg in messages:
                tables = []
                if msg.table_data:
                    try:
                        tables = json.loads(msg.table_data)
                    except (json.JSONDecodeError, TypeError):
                        pass
                result.append({
                    'role': msg.role,
                    'content': msg.content or '',
                    'tables': tables,
                    'model_accessed': msg.model_accessed or '',
                    'records_accessed': msg.records_accessed or 0,
                    'create_date': msg.create_date.strftime('%Y-%m-%d %H:%M') if msg.create_date else '',
                })
            return {'messages': result, 'total': total, 'has_more': has_more}
        except Exception as e:
            _logger.exception("Error loading messages")
            return {'messages': [], 'total': 0, 'has_more': False}
