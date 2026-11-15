"""Consistent numeric display without changing stored values or identifiers."""
import pandas as pd
from pandas.api.types import is_bool_dtype, is_numeric_dtype
import streamlit as st


INTEGER_FORMAT = "%,d"
DECIMAL_FORMAT = "%,.2f"
SCALED_PERCENT_FORMAT = "%.2f%%"
PROPORTION_COLUMNS = {"SOB", "SOB 合计", "自动比例", "实际使用比例"}
IDENTIFIER_COLUMNS = {"Material", "Store", "store", "benchmark", "销量来源货号", "curstatus", "cdc", "优先 CDC"}


def numeric_column_config(frame: pd.DataFrame, *, scaled_percentages=()):
    config = {}
    for column in frame.columns:
        values = frame[column]
        if column in IDENTIFIER_COLUMNS or is_bool_dtype(values.dtype):
            continue
        if column in scaled_percentages:
            number_format = SCALED_PERCENT_FORMAT
        elif column in PROPORTION_COLUMNS:
            number_format = "percent"  # Native fraction-to-percent format, two decimals.
        elif column == "理论分货量":
            number_format = DECIMAL_FORMAT
        elif is_numeric_dtype(values.dtype):
            present = values.dropna()
            number_format = INTEGER_FORMAT if present.mod(1).eq(0).all() else DECIMAL_FORMAT
        else:
            continue
        config[column] = st.column_config.NumberColumn(format=number_format)
    return config
