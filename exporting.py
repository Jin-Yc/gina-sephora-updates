"""Create a portable Excel download from the complete allocation details."""
from io import BytesIO

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


def details_to_excel(details: pd.DataFrame) -> bytes:
    if len(details) > 1_048_575 or len(details.columns) > 16_384:
        raise ValueError("明细数量超出单个 Excel 工作表的容量，请减少本次分货范围。")
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "门店分货与CDC明细"
    columns = list(details.columns)
    sheet.append(columns)
    integer_columns = {"allocation", "实际出库量", "缺货量", "调货数量", "基础 allocation",
                       "SKU DM", "门店 DM", "DM 加量", "最终分配量", "DM 需求", "DM 实际出库", "常规实际出库"}
    integer_columns.update(column for column in columns if column.endswith(" 出库"))
    integer_columns.update({"Mini", "Total Stock", "Order Quantity", "原始需求量", "需求量", "比例缩减量", "未满足需求量"})

    for row_number, values in enumerate(details.itertuples(index=False, name=None), start=2):
        for column_number, value in enumerate(values, start=1):
            if pd.isna(value):
                value = None
            elif hasattr(value, "item"):
                value = value.item()
            cell = sheet.cell(row_number, column_number, value)
            if isinstance(value, str):
                # Keep identifiers/leading zeroes and literal text, including strings
                # beginning with '='. User data must not become an Excel formula.
                cell.data_type = "s"
            column = columns[column_number - 1]
            if column in {"SOB", "自动比例", "实际使用比例"}:
                cell.number_format = "0.00%"
            elif column in integer_columns:
                cell.number_format = "0"
            elif column in {"Sales qty", "有效门店总销量", "理论分货量"}:
                cell.number_format = "0.####"

    header_fill = PatternFill("solid", fgColor="24476B")
    for cell in sheet[1]:
        cell.font = Font(name="微软雅黑", bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    sheet.row_dimensions[1].height = 32
    for index, column in enumerate(columns, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = (
            40 if column == "storename" else 28 if column == "状态"
            else 22 if column in {"有效门店总销量", "销量来源货号"} else 18
        )
    # Freeze the identifier columns through Store, as well as the header.
    frozen_columns = columns.index("Store") + 1 if "Store" in columns else 0
    sheet.freeze_panes = f"{get_column_letter(frozen_columns + 1)}2"
    sheet.auto_filter.ref = sheet.dimensions
    stream = BytesIO()
    workbook.save(stream)
    return stream.getvalue()
