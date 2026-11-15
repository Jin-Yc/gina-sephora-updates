"""Per-session, per-mode inputs and results, independent of widget cleanup."""
from dataclasses import dataclass, field

import pandas as pd
import streamlit as st

from gina_sephora.domain.allocation import AllocationResult
from gina_sephora.domain.existing_stock import ExistingStockPlan
from gina_sephora.data_io.validation import ValidationResult
from gina_sephora.services.workflows import AllocationMode


@dataclass(frozen=True)
class InputFile:
    name: str
    data: bytes


@dataclass
class PageState:
    files: dict[str, InputFile] = field(default_factory=dict)
    upload_generation: int = 0
    validation: ValidationResult | None = None
    allocation: AllocationResult | None = None
    excel: bytes | None = None
    calculation_error: str | None = None
    plan: ExistingStockPlan | None = None
    percentages: dict[str, float] = field(default_factory=dict)
    input_revision: int = 0
    ratio_notice: str | None = None
    percentage_table: pd.DataFrame | None = None
    percentage_editor_revision: int = 0

    def invalidate(self) -> None:
        self.validation = None
        self.plan = None
        self.percentages.clear()
        self.input_revision += 1
        self.ratio_notice = None
        self.percentage_table = None
        self.percentage_editor_revision = 0
        self.invalidate_results()

    def invalidate_results(self) -> None:
        self.allocation = None
        self.excel = None
        self.calculation_error = None


def page_state(mode: AllocationMode) -> PageState:
    key = f"{mode.id}_page_state"
    if key not in st.session_state:
        st.session_state[key] = PageState()
    return st.session_state[key]


def invalidate_page(mode: AllocationMode) -> None:
    page_state(mode).invalidate()


def capture_upload(mode: AllocationMode, kind: str, widget_key: str) -> None:
    state = page_state(mode)
    uploaded = st.session_state.get(widget_key)
    if uploaded is None:
        state.files.pop(kind, None)
    else:
        state.files[kind] = InputFile(uploaded.name, uploaded.getvalue())
    state.invalidate()


def clear_uploads(mode: AllocationMode) -> None:
    state = page_state(mode)
    state.files.clear()
    state.upload_generation += 1
    state.invalidate()
