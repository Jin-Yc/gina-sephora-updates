"""Existing-stock page: demand-based replenishment with per-item percentages."""
from ui.allocation_page import render_allocation_page
from workflows import EXISTING_STOCK

render_allocation_page(EXISTING_STOCK)
