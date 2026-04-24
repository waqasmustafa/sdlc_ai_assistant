# ── Odoo AI Assistant Module ─────────────────────────────────────
# Top-level package init. Imports sub-packages so Odoo discovers
# the models, controllers, and wizards defined in this module.
# Import order doesn't matter here — Odoo loads them all at startup.

from . import models
from . import controllers
from . import wizard
