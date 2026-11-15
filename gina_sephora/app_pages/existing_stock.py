"""Existing-stock page: demand-based replenishment with per-item percentages."""
from gina_sephora.ui.allocation_page import render_allocation_page
from gina_sephora.services.workflows import EXISTING_STOCK

render_allocation_page(EXISTING_STOCK)
