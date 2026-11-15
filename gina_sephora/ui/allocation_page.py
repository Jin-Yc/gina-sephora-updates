"""One shared page layout for new-stock and existing-stock allocation."""
import streamlit as st

from gina_sephora.domain.existing_stock import prepare_existing_stock
from gina_sephora.ui.existing_stock_controls import render_existing_stock_controls
from gina_sephora.ui.formatting import numeric_column_config
from gina_sephora.ui.results import render_results
from gina_sephora.ui.rules import EXISTING_STOCK_RULES, NEW_STOCK_RULES
from gina_sephora.ui.state import capture_upload, clear_uploads, invalidate_page, page_state
from gina_sephora.data_io.validation import ValidationResult, validate_uploads
from gina_sephora.services.workflows import AllocationMode, NEW_STOCK, calculate_for_mode


def render_options(mode: AllocationMode) -> tuple[bool, bool]:
    if mode != NEW_STOCK:
        st.info("按 Material 匹配各店需求：Mini + Sales qty − Total Stock − Order Quantity，小于 0 按 0。")
        st.checkbox("启用 DM 加量（现货不使用）", value=False, disabled=True, key="existing_stock_dm_disabled",
                    help="现货不执行 DM 加量；新货页面的 DM 功能继续保留。")
        return False, False
    use_benchmark = st.checkbox(
        "使用 benchmark 销量分货", key=f"{mode.id}_use_benchmark", persist_state="session",
        on_change=invalidate_page, args=(mode,),
        help="勾选后需要 SKU 表包含 benchmark 列（也支持 SOB benchmark 表头）。"
             "缺少或未填写 benchmark 会提示修正，不会自动切换到 Material。",
    )
    st.info("当前按 benchmark 的历史销量计算 SOB，并分配各 Material 自己的库存。"
            if use_benchmark else
            "当前按 Material 自身的历史销量分货。若该货号没有有效销量，将提示暂不分配。")
    use_dm = st.checkbox(
        "启用 DM 分配", key=f"{mode.id}_use_dm", persist_state="session",
        on_change=invalidate_page, args=(mode,),
        help="启用后，SKU DM 使用 315/251，门店 DM 使用 1/0（空白视为 0）。DM 优先从 CDC 库存出库，剩余库存按 SOB 分配；DM 无法全部满足时暂停该 SKU。",
    )
    if use_dm:
        st.info("DM 已启用：315 对当前全部门店各 1 件；251 仅对门店 DM=1 各 1 件。"
                "先保障 DM，剩余库存按 SOB 分配；常规与 DM 均扣库存，最终量不重复加 DM。")
    with st.expander("CDC 可分配库存比例与阈值（预留，暂未启用）"):
        st.checkbox("启用仓库库存限制", value=False, disabled=True,
                    key=f"{mode.id}_warehouse_limits_enabled")
        st.caption("当前所有控件均已禁用，不影响库存。预留规则：库存严格超过阈值后，按比例向下取整。")
        for column, warehouse in zip(st.columns(3), ["CDC2", "CDC3", "CDC5"]):
            with column:
                st.write(warehouse)
                st.number_input("触发阈值", min_value=0, value=2000, step=100,
                                disabled=True, key=f"{mode.id}_{warehouse}_threshold")
                st.number_input("可分配比例（%）", min_value=0, max_value=100,
                                value=80 if warehouse == "CDC2" else 100, step=1,
                                disabled=True, key=f"{mode.id}_{warehouse}_percentage")
    return use_benchmark, use_dm


def render_uploads(mode: AllocationMode) -> None:
    state = page_state(mode)
    left, right = st.columns(2)
    for column, kind, heading, label, help_text in (
        (left, "master", "1. 库存与门店清单", "选择 SKU & Store List 文件",
         "需要包含 SKU 和 Store list 两个工作表，文件名可以自行命名。"),
        (right, "sales", "2. 门店销售数据", "选择门店销售文件",
         "需要包含 Material、Store、Sales qty。支持系统导出的 .xls 文本表格。" if mode == NEW_STOCK else
         "需要 Material、Store、Mini、Sales qty、Total Stock、Order Quantity；支持系统导出的 .xls 文本表格。"),
    ):
        with column:
            st.subheader(heading)
            key = f"{mode.id}_{kind}_file_{state.upload_generation}"
            st.file_uploader(label, type=["xlsx", "xls"], key=key,
                             help=help_text + " 切换页面后保留文件，重新上传可替换。",
                             on_change=capture_upload, args=(mode, kind, key))
            saved = state.files.get(kind)
            if saved is not None:
                st.caption(f"当前使用：{saved.name}（已为本页保留）")
    if state.files:
        st.button("清除本页上传文件", key=f"{mode.id}_clear_uploads",
                  on_click=clear_uploads, args=(mode,))


def render_validation(result: ValidationResult, *, use_dm: bool) -> None:
    st.subheader("检查结果")
    if result.errors:
        st.error(f"检查未通过：发现 {len(result.errors)} 项问题，请修正文件后重新上传。")
        for error in result.errors:
            st.error(error)
    else:
        st.success("两份文件的基础检查通过。")
    for warning in result.warnings:
        st.warning(warning)
    if result.tables:
        for column, (name, table) in zip(st.columns(len(result.tables)), result.tables.items()):
            column.metric(f"{name}记录数", len(table))
        if result.warehouses:
            st.caption("库存仓库：" + "、".join(result.warehouses))
        with st.expander("查看数据预览（各表前 10 行）"):
            for name, table in result.tables.items():
                st.write(name)
                st.dataframe(table.head(10), hide_index=True, width="stretch", alt=f"{name}前 10 行预览",
                             column_config=numeric_column_config(table))
    if not result.errors and use_dm:
        stores = result.tables["Store list"]
        st.caption(f"当前门店共 {len(stores)} 家，其中 DM special 门店 {int(stores['DM'].eq(1).sum())} 家。"
                   "315/251 是规则类型，实际加量以当前门店清单及 DM 标记为准。")


def render_allocation_page(mode: AllocationMode) -> None:
    state = page_state(mode)
    is_new = mode == NEW_STOCK
    if is_new:
        st.write("上传两份文件，检查后计算 SOB、目标分货量和各 CDC 实际出库数量。")
    else:
        st.write("上传两份文件，先检查并计算门店需求，再按 item 设置比例、计算 CDC 出库并导出。")
    with st.expander("分货规则说明", expanded=True):
        st.markdown(NEW_STOCK_RULES if is_new else EXISTING_STOCK_RULES)
    use_benchmark, use_dm = render_options(mode)
    render_uploads(mode)
    ready = "master" in state.files and "sales" in state.files
    if not ready:
        st.info("请分别上传两份文件。")
    clicked = st.button(
        "检查并计算分货" if is_new else "检查文件并计算需求",
        key=f"{mode.id}_check", type="primary", disabled=not ready,
    )
    if clicked and ready:
        state.invalidate()
        with st.spinner("正在读取并检查文件……"):
            state.validation = validate_uploads(
                state.files["master"].data, state.files["sales"].data,
                use_benchmark=use_benchmark, use_dm=use_dm,
                existing_stock=not is_new,
            )
    result = state.validation
    if result is None:
        return
    render_validation(result, use_dm=use_dm)
    if result.errors:
        return
    calculate = clicked and is_new
    if not is_new:
        if state.plan is None:
            state.plan = prepare_existing_stock(result)
        calculate = render_existing_stock_controls()
    if calculate:
        state.invalidate_results()
        progress = st.progress(0.0, text="开始计算分货……")

        def report_progress(done: int, total: int, material: str) -> None:
            progress.progress(done / total, text=f"已完成 {done}/{total} 个 SKU，当前货号：{material}")

        try:
            state.allocation = calculate_for_mode(
                mode, result, use_benchmark=use_benchmark, use_dm=use_dm, on_progress=report_progress,
                percentages=state.percentages, existing_plan=state.plan,
            )
        except ValueError as error:
            state.calculation_error = str(error)
        finally:
            progress.empty()
    if state.calculation_error:
        st.error(f"分货计算未完成：{state.calculation_error}")
    if mode.can_calculate:
        render_results(mode, state)
