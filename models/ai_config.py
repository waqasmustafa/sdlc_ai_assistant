# ══════════════════════════════════════════════════════════════
# AI Configuration — Groq & OpenAI
# ══════════════════════════════════════════════════════════════

from odoo import models, fields, api
from odoo.exceptions import ValidationError


class AiConfig(models.Model):
    _name = 'ai.config'
    _description = 'AI Assistant Configuration'

    name = fields.Char(required=True, default='Default Configuration')
    active = fields.Boolean(default=True)

    provider = fields.Selection([
        ('groq', 'Groq Cloud'),
        ('openai', 'OpenAI'),
    ], string='AI Provider', default='groq', required=True)

    # ── Groq settings ──────────────────────────────────────────
    groq_api_key = fields.Char(
        string='Groq API Key',
        help='Free API key from https://console.groq.com. Starts with gsk_...',
    )
    groq_model = fields.Selection(
        selection='_get_groq_model_selection',
        string='Groq Model',
        default='llama-3.3-70b-versatile',
    )

    # ── OpenAI settings ────────────────────────────────────────
    openai_api_key = fields.Char(
        string='OpenAI API Key',
        help='API key from https://platform.openai.com. Starts with sk-...',
    )
    openai_model = fields.Selection([
        ('gpt-4o', 'GPT-4o (Smartest)'),
        ('gpt-4o-mini', 'GPT-4o Mini (Fast & Cheap)'),
        ('gpt-4-turbo', 'GPT-4 Turbo'),
        ('gpt-3.5-turbo', 'GPT-3.5 Turbo'),
    ], string='OpenAI Model', default='gpt-4o')

    @api.model
    def _get_groq_model_selection(self):
        return self.GROQ_MODELS

    def action_test_connection(self):
        """Verify that the API key and model work correctly."""
        self.ensure_one()
        provider = self.env['ai.provider'].sudo()
        success, message = provider.test_connection(self)
        
        if success:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Connection Successful',
                    'message': message,
                    'type': 'success',
                    'sticky': False,
                }
            }
        else:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Connection Failed',
                    'message': message,
                    'type': 'danger',
                    'sticky': True,
                }
            }

    # ── Common settings ────────────────────────────────────────
    temperature = fields.Float(default=0.1, help='Lower = more factual. 0.0-0.3 recommended.')
    max_tokens = fields.Integer(default=1024, help='Maximum response length')
    timeout = fields.Integer(default=60, help='Request timeout in seconds')

    @api.constrains('temperature')
    def _check_temperature(self):
        for rec in self:
            if not (0 <= rec.temperature <= 2):
                raise ValidationError("Temperature must be between 0 and 2")

    @api.model
    def get_active_config(self):
        """Return the active AI configuration."""
        config = self.search([('active', '=', True)], limit=1)
        return config or False

    # Groq models — each has its own 100K tokens/day free limit
    GROQ_MODELS = [
        ('llama-3.3-70b-versatile', 'Llama 3.3 70B (Smart)'),
        ('meta-llama/llama-4-scout-17b-16e-instruct', 'Llama 4 Scout 17B (New)'),
        ('moonshotai/kimi-k2-instruct', 'Kimi K2 (Powerful)'),
        ('openai/gpt-oss-120b', 'GPT-OSS 120B (Largest)'),
        ('openai/gpt-oss-20b', 'GPT-OSS 20B'),
        ('llama-3.1-8b-instant', 'Llama 3.1 8B (Fast)'),
    ]

    # Models that don't support Groq's response_format json_object
    # These still output JSON fine, just can't use the forced mode
    # Models that don't reliably support Groq's response_format json_object
    NO_JSON_MODE_MODELS = {
        'openai/gpt-oss-120b',
        'openai/gpt-oss-20b',
        'moonshotai/kimi-k2-instruct',
        'meta-llama/llama-4-scout-17b-16e-instruct',
    }

    @api.model
    def get_available_models(self):
        """Return available model options based on active provider."""
        config = self.get_active_config()
        if not config:
            return []
        
        if config.provider == 'openai':
            if not config.openai_api_key:
                return []
            # Return list of tuples converted to dict for frontend
            field = self._fields['openai_model']
            return [{'model': m[0], 'label': m[1]} for m in field.selection]
        else:
            if not config.groq_api_key:
                return []
            return [{'model': m, 'label': l} for m, l in self.GROQ_MODELS]

    @api.model
    def get_fallback_models(self, exclude_model=None):
        """Return ordered list of models to try when one hits rate limit."""
        return [m for m, l in self.GROQ_MODELS if m != exclude_model]
