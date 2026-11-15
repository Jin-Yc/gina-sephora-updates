"""Native editable item table with per-item percentage validation."""
import math

import pandas as pd
import streamlit as st

from existing_stock import ExistingStockPlan, manual_cap_percent
from ui.formatting import INTEGER_FORMAT, SCALED_PERCENT_FORMAT, numeric_column_config
from ui.state import page_state
from workflows import EXISTING_STOCK


MANUAL_COLUMN = "手动比例（%）"
STORE_RECORDS_COLUMN = "门店记录（完整 / 缺失）"


def build_percentage_table(plan: ExistingStockPlan, percentages: dict[str, float]) -> pd.DataFrame:
    columns = ["Material", "商品名称", *plan.warehouses, "总库存", "需求量合计",
               "需求记录门店数", "缺失记录门店数", "自动比例"]
    table = plan.items[columns].copy().set_index("Material", drop=False)
    table[STORE_RECORDS_COLUMN] = [
        f"{int(complete):,} / {int(missing):,}"
        for complete, missing in table[["需求记录门店数", "缺失记录门店数"]].itertuples(index=False, name=None)
    ]
    table = table.drop(columns=["需求记录门店数", "缺失记录门店数"])
    table = table[["Material", "商品名称", *plan.warehouses, "总库存", "需求量合计",
                   STORE_RECORDS_COLUMN, "自动比例"]]
    table["自动比例"] *= 100
    table[MANUAL_COLUMN] = pd.Series(
        [percentages.get(material, float("nan")) for material in table["Material"]],
        index=table.index, dtype="float64",
    )
    return table


def normalize_percentages(
    plan: ExistingStockPlan, edited: pd.DataFrame, previous: dict[str, float],
) -> tuple[dict[str, float], list[str]]:
    """Only read the editable column; identities and limits come from the plan."""
    percentages, notices = {}, []
    for position, (_, item) in enumerate(plan.items.iterrows()):
        material = item["Material"]
        value = edited.iloc[position][MANUAL_COLUMN]
        if value is None or pd.isna(value):
            continue
        if int(item["需求量合计"]) == 0:
            notices.append(f"{material} 无已知正需求，手动比例已清空并恢复自动。")
            continue
        try:
            if isinstance(value, bool):
                raise ValueError
            number = float(value)
            if not math.isfinite(number):
                raise ValueError
        except (TypeError, ValueError):
            if material in previous:
                percentages[material] = previous[material]
            notices.append(f"{material} 的手动比例不是有效数字，已恢复上次设置。")
            continue
        cap = manual_cap_percent(int(item["总库存"]), int(item["需求量合计"]))
        bounded = round(min(cap, max(0.0, number)), 2)
        percentages[material] = bounded
        if number < 0 or number > cap:
            notices.append(f"{material} 的手动比例已回退为 {bounded:.2f}%，有效范围为 0–{cap:.2f}%。")
        elif number != bounded:
            notices.append(f"{material} 的手动比例已按 0.01 个百分点精度调整为 {bounded:.2f}%。")
    return percentages, notices


def render_store_details(plan: ExistingStockPlan, input_revision: int) -> None:
    panel = st.expander("查看单个 item 的门店需求明细", key=f"existing_stock_details_{input_revision}",
                        on_change="rerun")
    if not panel.open:
        return
    with panel:
        names = dict(zip(plan.items["Material"], plan.items["商品名称"]))
        material = st.selectbox(
            "选择 item", plan.items["Material"].tolist(),
            format_func=lambda value: f"{value} · {names[value]}",
            key=f"existing_stock_details_item_{input_revision}", persist_state="session",
        )
        columns = ["Store", "curstatus", "storename", "优先 CDC", "Mini", "Sales qty", "Total Stock",
                   "Order Quantity", "原始需求量", "需求量", "数据完整"]
        details = plan.details.loc[plan.details["Material"].eq(material), columns]
        st.dataframe(details, column_config=numeric_column_config(details),
                     hide_index=True, width="stretch", alt=f"{material} 的门店需求明细")


def render_existing_stock_controls() -> bool:
    state = page_state(EXISTING_STOCK)
    plan = state.plan
    if plan is None:
        return False
    st.subheader("3. 各 item 库存、需求与分配比例")
    st.caption("每行一个 item，仅最后一列“手动比例（%）”可编辑：填 70 表示 70%，填 0 暂不分配，"
               "留空使用自动比例。可连续编辑或复制粘贴多个 item；更改后请重新计算分货。")
    key = f"existing_stock_percentages_{state.input_revision}_{state.percentage_editor_revision}"
    if state.percentage_table is None:
        state.percentage_table = build_percentage_table(plan, state.percentages)
    elif key not in st.session_state:
        # The native editor is unmounted on a page switch. Remount from the
        # accepted per-item settings, without replaying edits over a new base.
        state.percentage_editor_revision += 1
        key = f"existing_stock_percentages_{state.input_revision}_{state.percentage_editor_revision}"
        state.percentage_table = build_percentage_table(plan, state.percentages)
    edited = st.data_editor(
        state.percentage_table.copy(), key=key, hide_index=True, width="stretch", num_rows="fixed",
        disabled=[column for column in state.percentage_table.columns if column != MANUAL_COLUMN],
        column_config={
            **numeric_column_config(state.percentage_table, scaled_percentages={"自动比例", MANUAL_COLUMN}),
            "Material": st.column_config.TextColumn("Item / Material", pinned=True),
            "商品名称": st.column_config.TextColumn(width="medium"),
            "需求量合计": st.column_config.NumberColumn("已知需求量", format=INTEGER_FORMAT),
            STORE_RECORDS_COLUMN: st.column_config.TextColumn(
                help="左侧是需求记录完整的门店数，右侧是缺少需求记录的门店数。", alignment="right"),
            "自动比例": st.column_config.NumberColumn("自动比例上限", format=SCALED_PERCENT_FORMAT),
            MANUAL_COLUMN: st.column_config.NumberColumn(
                MANUAL_COLUMN, min_value=0.0, max_value=100.0, step=0.01, format=SCALED_PERCENT_FORMAT,
                help="留空使用自动比例；输入 70 表示 70%。实际值不能超过该行自动比例上限，超出后回退并提示。",
            ),
        },
        alt="按 item 查看库存、需求和自动比例，并编辑最后一列的手动比例",
    )
    percentages, notices = normalize_percentages(plan, edited, state.percentages)
    if percentages != state.percentages:
        state.percentages = percentages
        state.ratio_notice = None
        state.invalidate_results()
    if notices:
        state.ratio_notice = "\n\n".join(notices)
        # A fresh editor displays corrected values. Its input snapshot stays
        # stable during ordinary edits, avoiding the data_editor feedback loop.
        state.percentage_table = None
        state.percentage_editor_revision += 1
        st.rerun()
    if state.ratio_notice:
        st.warning(state.ratio_notice)
    st.caption("自动比例只根据记录完整门店的需求计算；门店记录列按“完整 / 缺失”显示。"
               "手动值范围为 0–该行自动上限，精确到 0.01 个百分点；留空时保留完整自动比例。")
    render_store_details(plan, state.input_revision)
    return st.button("计算现货分货", key="existing_stock_calculate", type="primary")
