# ── ai_conversation.py ───────────────────────────────────────────
# Stores chat conversations and individual messages.
# Each user gets their own conversation threads.
# Messages are saved for two reasons:
#   1. Memory — the AI can reference earlier messages in the same chat
#   2. Audit — admins can review what data was accessed and when

from odoo import models, fields, api


class AiConversation(models.Model):
    """
    Conversation model.
    Stores chat history for:
    1. Memory — AI can reference previous messages in the same conversation
    2. Audit trail — who asked what, when, and what data was accessed
    """
    _name = 'ai.conversation'
    _description = 'AI Conversation'
    _order = 'create_date desc'

    name = fields.Char(compute='_compute_name', store=True)
    custom_name = fields.Char(help='User-defined conversation name (overrides auto-generated)')
    user_id = fields.Many2one('res.users', default=lambda self: self.env.user, required=True)
    message_ids = fields.One2many('ai.message', 'conversation_id', string='Messages')
    message_count = fields.Integer(compute='_compute_message_count')
    active = fields.Boolean(default=True)

    # ── Computed Fields ──────────────────────────────────────────

    # Uses custom_name if set, otherwise auto-generates from first user message
    @api.depends('message_ids', 'custom_name')
    def _compute_name(self):
        for rec in self:
            if rec.custom_name:
                rec.name = rec.custom_name
            else:
                first_msg = rec.message_ids.filtered(lambda m: m.role == 'user')[:1]
                if first_msg:
                    text = first_msg.content[:60]
                    rec.name = f"{text}..." if len(first_msg.content) > 60 else text
                else:
                    rec.name = f"Conversation #{rec.id or 'New'}"

    @api.depends('message_ids')
    def _compute_message_count(self):
        for rec in self:
            rec.message_count = len(rec.message_ids)

    # ── Public API ─────────────────────────────────────────────────

    def get_history_for_ai(self):
        """Return conversation history in the format the AI provider expects."""
        self.ensure_one()
        messages = self.message_ids.sorted('create_date')
        return [
            {'role': msg.role, 'content': msg.content}
            for msg in messages
            if msg.role in ('user', 'assistant')
        ]


# ── AiMessage ────────────────────────────────────────────────────
# One row per chat bubble. Linked to a conversation via Many2one.
# Stores metadata for auditing (which model was queried, how many
# records, how long the AI took to respond, etc.)

class AiMessage(models.Model):
    """Individual message in a conversation."""
    _name = 'ai.message'
    _description = 'AI Message'
    _order = 'create_date asc'

    # ── Core Fields ─────────────────────────────────────────────
    conversation_id = fields.Many2one('ai.conversation', required=True, ondelete='cascade')
    role = fields.Selection([
        ('user', 'User'),           # What the human typed
        ('assistant', 'Assistant'),  # AI's reply
        ('system', 'System'),       # Error or status messages
    ], required=True)
    content = fields.Text(required=True)

    # ── Audit / Metadata Fields ─────────────────────────────────
    model_accessed = fields.Char(help='Which Odoo model was queried for this message')
    records_accessed = fields.Integer(help='How many records were accessed')
    tokens_used = fields.Integer()
    ai_provider = fields.Char()        # "ollama", "openai", or "direct"
    ai_model = fields.Char()           # e.g. "qwen2.5:1.5b", "gpt-4o-mini"
    intent_confidence = fields.Float()
    response_time = fields.Float(help='Response time in seconds')
    # NEW: Store table data as JSON so historical messages render with tables
    table_data = fields.Text(help='JSON-serialized table data for data responses')
