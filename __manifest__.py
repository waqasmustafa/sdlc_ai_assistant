# ── Module Manifest ──────────────────────────────────────────────
# This file tells Odoo everything it needs to know to install the
# AI Assistant module: metadata, dependencies, data files, and assets.
{
    'name': 'Odoo AI Assistant',
    'version': '18.0.1.1.0',
    'category': 'Productivity',
    'summary': 'Privacy-first AI assistant with OpenAI and Groq support for Odoo 18',
    'description': """
        AI Assistant for Odoo
        =====================
        Seamlessly interact with your Odoo data using natural language. This module bridges the gap between your ERP and advanced AI models.

        Key Features:
        • Dual Provider Support: Choose between OpenAI (GPT-4o) and Groq Cloud (Llama 3.3).
        • Natural Language Queries: Ask questions about your CRM, Sales, HR, Inventory, and Knowledge Base.
        • Smart Intent Detection: Automatically maps user questions to the correct Odoo models.
        • Privacy-First: Fetches data via Odoo ORM, respecting all record rules and access rights.
        • Test Connection: Integrated tool to verify your API credentials instantly.
        • Dynamic Knowledge Base: Now supports querying Odoo Knowledge articles.
        • Optimized for Odoo 18: Clean, modern interface using OWL components.

        Supported Models:
        • Contacts & Partners
        • CRM Leads & Opportunities
        • Sales Orders & Quotations
        • Employees & Departments
        • Products & Categories
        • Invoices & Payments
        • Stock Transfers & Warehouses
        • Calendar Events & Meetings
        • Knowledge Articles

        Configuration:
        • Simple setup in Settings > AI Configuration.
        • Enter your API key (OpenAI or Groq).
        • Select your preferred model and start chatting!
    """,
    'author': 'Waqas Mustafa',
    'website': 'https://www.linkedin.com/in/waqas-mustafa-ba5701209/',
    'support': 'mustafawaqas0@gmail.com',

    # ── Dependencies ────────────────────────────────────────────
    # Other Odoo modules that must be installed first.
    # We need these because we query their models (e.g. crm.lead,
    # sale.order, hr.employee, stock.picking, etc.)
    'depends': [
        'base',
        'mail',
        'crm',
        'sale_management',
        'hr',
        'stock',
        'account',
        'product',
        'calendar',
        'knowledge',
    ],

    # ── Data Files ──────────────────────────────────────────────
    # Loaded in order during install/upgrade:
    # 1. Security rules & access rights (must come first)
    # 2. Seed data (predefined intents the AI recognises)
    # 3. UI views and menus
    'data': [
        'security/ai_security.xml',
        'security/ir.model.access.csv',
        'data/ai_intent_data.xml',
        'views/ai_config_views.xml',
        'views/ai_conversation_views.xml',
        'views/ai_assistant_action.xml',
        'views/ai_menus.xml',
        'views/res_users_views.xml',
    ],

    # ── Frontend Assets ─────────────────────────────────────────
    # JS + XML loaded in the backend (web client). These power
    # the chat UI that users interact with.
    'assets': {
        'web.assets_backend': [
            'sdlc_ai_assistant/static/src/css/ai_chat.css',
            'sdlc_ai_assistant/static/src/js/ai_chat_widget.js',
            'sdlc_ai_assistant/static/src/js/ai_chat_action.js',
            'sdlc_ai_assistant/static/src/xml/ai_chat_templates.xml',
        ],
    },

    # ── Flags ───────────────────────────────────────────────────
    'installable': True,
    'application': True,
    'auto_install': False,
    'license': 'LGPL-3',
    "images": ["static/description/banner.jpg"],
}
