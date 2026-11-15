"""Shared allocation result and Excel download presentation."""
import streamlit as st

from exporting import details_to_excel
from ui.formatting import INTEGER_FORMAT, numeric_column_config
from workflows import AllocationMode, NEW_STOCK
from ui.state import PageState


def render_results(mode: AllocationMode, state: PageState) -> None:
    allocation = state.allocation
    result = state.validation
    if allocation is None or result is None:
        return
    st.subheader("分货结果")
    for notice in allocation.notices:
        st.warning(notice)
    if not allocation.new_stores.empty:
        st.warning(
            f"发现 {len(allocation.new_stores)} 家新店／无历史销售记录门店，"
            "其 allocation 保留 -10，不参与常规 SOB 分货；如适用 DM，仍纳入库存分配检查。"
        )
        with st.expander("查看新店／无历史销售记录门店"):
            st.dataframe(allocation.new_stores, hide_index=True, width="stretch", alt="无历史销售记录门店",
                         column_config=numeric_column_config(allocation.new_stores))
    st.write("SKU 汇总")
    st.caption((
        "用于按货号核对总库存、实际出库、缺货和各 CDC 剩余库存。"
        "已确认的结果应满足：总库存 = 实际出库合计 + 剩余库存；"
        "实际出库已包含常规和 DM，最终分配量等于实际出库量。"
        "DM 需求与实际出库分别展示；DM 无法全部满足时暂停该 SKU。"
    ) if mode == NEW_STOCK else (
        "需求量为负时按 0；比例缩减量是按比例主动减少的数量，缺货量是目标量中受 CDC 限制未能出库的数量。"
        "未满足需求量包含这两部分。已确认结果满足：总库存 = 实际出库合计 + 剩余库存。"
        "缺失需求不计入需求合计；现货不使用 benchmark、SOB 或 DM 加量。"
    ))
    st.dataframe(allocation.summary, hide_index=True, width="stretch", alt="SKU 分货汇总",
                 column_config={**numeric_column_config(allocation.summary),
                                "目标量合计（不含 -10）": st.column_config.NumberColumn(
                                    "基础目标量合计（不含 -10）", format=INTEGER_FORMAT)})
    st.write("门店分货与 CDC 出库明细")
    st.dataframe(
        allocation.details, hide_index=True, width="stretch", alt="门店分货与 CDC 出库明细",
        column_config=numeric_column_config(allocation.details),
    )
    try:
        if state.excel is None:
            state.excel = details_to_excel(allocation.details)
        excel_data = state.excel
    except ValueError as error:
        st.error(f"明细导出失败：{error}")
    else:
        st.download_button(
            "导出门店分货与 CDC 明细（Excel）", data=excel_data,
            file_name="门店分货与CDC明细.xlsx" if mode == NEW_STOCK else "现货分货与CDC明细.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            on_click="ignore", key=f"{mode.id}_download_allocation_details",
        )
        st.caption("导出包含全部明细、benchmark、SOB、常规目标与实际出库、DM 需求与实际出库、最终量及各 CDC 出库。"
                   "兼容列“DM 加量”表示已包含在实际出库中的 DM 实际数量，不可再次相加。"
                   if mode == NEW_STOCK else
                   "导出包含各店需求公式字段、自动及实际比例、目标量、比例缩减、实际出库、CDC 缺货和缺失数据状态。")
