"""Read and validate uploaded data without any Streamlit or allocation logic."""
import csv
from dataclasses import dataclass, field
from io import BytesIO, StringIO
import re
import unicodedata

import pandas as pd


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    tables: dict[str, pd.DataFrame] = field(default_factory=dict)
    warehouses: list[str] = field(default_factory=list)


def _label(value) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value)).strip().split()).casefold()


def _identifier(value) -> str:
    if pd.isna(value):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    # Remove Excel's numeric suffix without stripping meaningful leading zeroes.
    return re.sub(r"^(\d+)\.0+$", r"\1", text)


def _rows(mask: pd.Series) -> str:
    numbers = list(mask.index[mask])
    preview = "、".join(str(number) for number in numbers[:8])
    return preview + (f" 等，共 {len(numbers)} 行" if len(numbers) > 8 else "")


def _excel(data: bytes) -> pd.ExcelFile:
    if not data:
        raise ValueError("文件为空。")
    if data.startswith(b"PK"):
        engine = "openpyxl"
    elif data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        engine = "xlrd"
    else:
        raise ValueError("需要真正的 Excel 工作簿（.xlsx 或 .xls）。")
    return pd.ExcelFile(BytesIO(data), engine=engine)


def _read_sheet(book: pd.ExcelFile, name: str) -> pd.DataFrame:
    return pd.read_excel(book, sheet_name=name, header=None, dtype=object, keep_default_na=False)


def _prepare(raw, required, title, result, *, warehouse_columns=False):
    if raw.empty:
        result.errors.append(f"{title}为空。")
        return None
    names = [_label(value) for value in raw.iloc[0]]
    requested = {_label(column): column for column in required}
    if warehouse_columns:
        for name in names:
            if re.fullmatch(r"cdc\d+", name):
                requested[name] = name.upper()
    missing = [column for key, column in requested.items() if key not in names]
    duplicates = [column for key, column in requested.items() if names.count(key) > 1]
    if missing:
        result.errors.append(f"{title}缺少必需列：{'、'.join(missing)}。")
    if duplicates:
        result.errors.append(f"{title}存在重复的必需列：{'、'.join(duplicates)}。")
    if missing or duplicates:
        return None
    if warehouse_columns and len(requested) == len(required):
        result.errors.append(f"{title}至少需要一个仓库库存列，例如 CDC2。")
        return None
    data = raw.iloc[1:, [names.index(key) for key in requested]].copy()
    data.columns = list(requested.values())
    data.index = raw.index[1:] + 1  # Physical Excel/text row numbers, including the header.
    data.index.name = "行号"
    # Ignore entirely blank source rows, not rows missing only the relevant fields.
    nonempty = raw.iloc[1:].apply(
        lambda column: column.map(lambda value: "" if pd.isna(value) else str(value).strip())
    ).ne("").any(axis=1)
    data = data.loc[nonempty.to_numpy()]
    if data.empty:
        result.errors.append(f"{title}只有表头，没有数据。")
    result.tables[title] = data
    return data


def _check_ids(data, columns, title, result):
    for column in columns:
        data[column] = data[column].map(_identifier)
        missing = data[column].eq("")
        if missing.any():
            result.errors.append(f"{title}的 {column} 不能为空，原表第 {_rows(missing)} 行。")


def _check_unique(data, column, title, result):
    duplicates = data[column].ne("") & data[column].duplicated(keep=False)
    if duplicates.any():
        result.errors.append(f"{title}的 {column} 有重复值，原表第 {_rows(duplicates)} 行。")


def _check_numbers(data, columns, title, result, *, inventory=False):
    for column in columns:
        values = pd.to_numeric(data[column], errors="coerce")
        invalid = values.isna() | values.isin([float("inf"), float("-inf")])
        invalid |= data[column].map(lambda value: isinstance(value, bool))
        if invalid.any():
            result.errors.append(
                f"{title}的 {column} 必须填写有效数字（空白不视为 0），原表第 {_rows(invalid)} 行。"
            )
        negative = ~invalid & values.lt(0)
        fractional = ~invalid & values.mod(1).ne(0)
        if inventory:
            if (negative | fractional).any():
                result.errors.append(
                    f"{title}的 {column} 库存必须为非负整数，原表第 {_rows(negative | fractional)} 行。"
                )
        else:
            if negative.any():
                result.warnings.append(
                    f"销售数据存在负销售量，原表第 {_rows(negative)} 行；请确认是否为退货等情况。"
                )
            if fractional.any():
                result.warnings.append(f"销售数据存在非整数销售量，原表第 {_rows(fractional)} 行。")
        data[column] = values


def _check_dm(data, title, allowed, result, *, check=True):
    values = data["DM"].map(_identifier).replace("", "0")
    numeric = pd.to_numeric(values, errors="coerce")
    invalid = ~numeric.isin(allowed)
    if check and invalid.any():
        result.errors.append(
            f"{title} 的 DM 只能填写 {'、'.join(map(str, sorted(allowed)))}（空白视为 0），"
            f"原表第 {_rows(invalid)} 行。"
        )
    data["DM"] = numeric if check or numeric.notna().all() else values


def _validate_master(data: bytes, result: ValidationResult, *, use_benchmark=False, use_dm=False,
                     existing_stock=False):
    with _excel(data) as book:
        selected = {}
        for expected in ("SKU", "Store list"):
            matches = [name for name in book.sheet_names if _label(name) == _label(expected)]
            if len(matches) != 1:
                result.errors.append(f"库存与门店清单需要唯一的 {expected} 工作表。")
            else:
                selected[expected] = matches[0]
        if "SKU" in selected:
            raw = _read_sheet(book, selected["SKU"])
            if not raw.empty:
                raw.iloc[0] = raw.iloc[0].map(
                    lambda value: "benchmark" if _label(value) == "sob benchmark" else value
                )
            required = ["Material", "Total QTY"]
            if existing_stock and not raw.empty:
                # Descriptions are optional display metadata, never a calculation key.
                raw.iloc[0] = raw.iloc[0].map(
                    lambda value: "Description" if _label(value) == "material description" else value
                )
                if "description" in raw.iloc[0].map(_label).values:
                    required.append("Description")
            if not existing_stock and (use_benchmark or (not raw.empty and "benchmark" in raw.iloc[0].map(_label).values)):
                required.append("benchmark")
            if not existing_stock and (use_dm or (not raw.empty and "dm" in raw.iloc[0].map(_label).values)):
                required.append("DM")
            sku = _prepare(raw, required,
                           "SKU", result, warehouse_columns=True)
            if sku is not None and not sku.empty:
                result.warehouses = [column for column in sku if re.fullmatch(r"CDC\d+", column)]
                _check_ids(sku, ["Material"], "SKU", result)
                _check_unique(sku, "Material", "SKU", result)
                if "DM" in sku:
                    _check_dm(sku, "SKU", {0, 251, 315}, result, check=use_dm)
                if "benchmark" in sku:
                    sku["benchmark"] = sku["benchmark"].map(_identifier)
                    if use_benchmark:
                        _check_ids(sku, ["benchmark"], "SKU", result)
                quantities = ["Total QTY", *result.warehouses]
                _check_numbers(sku, quantities, "SKU", result, inventory=True)
                valid = sku[quantities].notna().all(axis=1)
                mismatch = valid & sku["Total QTY"].ne(sku[result.warehouses].sum(axis=1))
                if mismatch.any():
                    result.errors.append(
                        f"SKU 的 Total QTY 不等于各仓库库存之和，原表第 {_rows(mismatch)} 行。"
                    )
        if "Store list" in selected:
            raw = _read_sheet(book, selected["Store list"])
            required = ["store", "curstatus", "storename", "cdc"]
            if not existing_stock and (use_dm or (not raw.empty and "dm" in raw.iloc[0].map(_label).values)):
                required.append("DM")
            stores = _prepare(raw, required,
                              "Store list", result)
            if stores is not None and not stores.empty:
                _check_ids(stores, ["store", "cdc"], "Store list", result)
                _check_unique(stores, "store", "Store list", result)
                if "DM" in stores:
                    _check_dm(stores, "Store list", {0, 1}, result, check=use_dm)
                stores["cdc"] = stores["cdc"].str.upper()
                if result.warehouses:
                    unknown = stores["cdc"].ne("") & ~stores["cdc"].isin(result.warehouses)
                    if unknown.any():
                        names = "、".join(sorted(stores.loc[unknown, "cdc"].unique()))
                        result.errors.append(f"Store list 中的优先仓库没有对应库存列：{names}。")


def _read_sales(data: bytes) -> pd.DataFrame:
    if not data:
        raise ValueError("文件为空。")
    if data.startswith((b"PK", b"\xd0\xcf\x11\xe0")):
        with _excel(data) as book:
            matches = []
            for name in book.sheet_names:
                header = pd.read_excel(book, sheet_name=name, header=None, nrows=1,
                                       dtype=object, keep_default_na=False)
                if not header.empty and {"material", "store", "sales qty"}.issubset(
                    {_label(value) for value in header.iloc[0]}
                ):
                    matches.append(name)
            if len(matches) != 1:
                raise ValueError("需要且只能有一个包含 Material、Store、Sales qty 的销售工作表。")
            return _read_sheet(book, matches[0])
    encodings = ("utf-16",) if data.startswith((b"\xff\xfe", b"\xfe\xff")) else ("utf-8-sig", "gb18030")
    for encoding in encodings:
        try:
            text = data.decode(encoding)
        except UnicodeError:
            continue
        lines = text.splitlines()
        if not lines or "\t" not in lines[0]:
            raise ValueError("销售文件需要是 Excel 或制表符分隔的系统导出表格。")
        # Preserve duplicate irrelevant headers (e.g. Brand); validate required ones separately.
        rows = list(csv.reader(StringIO(text), delimiter="\t"))
        width = len(rows[0])
        if any(len(row) > width for row in rows):
            raise ValueError("销售表格有行的列数超过表头，请检查导出文件。")
        return pd.DataFrame(rows)
    raise ValueError("无法识别销售文件的文本编码，请重新导出为 Excel 或 UTF-16 表格。")


def _validate_sales(data: bytes, result: ValidationResult, *, use_benchmark=False, use_dm=False,
                    existing_stock=False):
    raw = _read_sales(data)
    required = ["Material", "Store", "Sales qty"]
    if existing_stock:
        required += ["Mini", "Total Stock", "Order Quantity"]
        if not raw.empty:
            raw.iloc[0] = raw.iloc[0].map(
                lambda value: "Total Stock" if _label(value) == "total stcok" else value
            )
            if "material description" in raw.iloc[0].map(_label).values:
                required.append("Material Description")
    sales = _prepare(raw, required, "销售数据", result)
    if sales is None or sales.empty:
        return
    _check_ids(sales, ["Material", "Store"], "销售数据", result)
    if existing_stock:
        _check_numbers(sales, ["Mini", "Total Stock", "Order Quantity"], "销售数据", result, inventory=True)
        _check_existing_sales_quantity(sales, result)
    else:
        _check_numbers(sales, ["Sales qty"], "销售数据", result)
    duplicates = sales.duplicated(["Material", "Store"], keep=False)
    if duplicates.any():
        if existing_stock:
            result.errors.append(
                f"现货数据的 Material + Store 必须唯一，重复记录在原表第 {_rows(duplicates)} 行；"
                "请修正后重新上传，避免重复扣减库存和订单。"
            )
        else:
            result.warnings.append(
                f"销售数据存在重复的 Material + Store 组合，原表第 {_rows(duplicates)} 行；"
                "请确认是有效的多条明细，计算时会按销量汇总。"
            )
    if "SKU" in result.tables and "Store list" in result.tables:
        sku = result.tables["SKU"]
        stores = result.tables["Store list"]
        source_column = "benchmark" if use_benchmark else "Material"
        if source_column not in sku:
            return
        outside = ~sales["Material"].isin(sku[source_column]) | ~sales["Store"].isin(stores["store"])
        if outside.any():
            result.warnings.append(
                f"销售数据中有 {int(outside.sum())} 条记录的货号不在当前{source_column}销量来源范围内，"
                "或店号不在门店清单中，仅提示范围差异。"
            )


def _check_existing_sales_quantity(sales, result):
    values = pd.to_numeric(sales["Sales qty"], errors="coerce")
    invalid = values.isna() | values.isin([float("inf"), float("-inf")])
    invalid |= sales["Sales qty"].map(lambda value: isinstance(value, bool))
    invalid |= values.mod(1).ne(0)
    if invalid.any():
        result.errors.append(f"现货 Sales qty 必须为整数（允许负销量），原表第 {_rows(invalid)} 行。")
    sales["Sales qty"] = values
    if values.lt(0).any():
        result.warnings.append("现货数据包含负销量，已按确认规则直接参与需求公式计算，需求小于 0 时按 0。")


def validate_uploads(master_data: bytes, sales_data: bytes, *, use_benchmark=False, use_dm=False,
                     existing_stock=False) -> ValidationResult:
    if existing_stock:
        use_benchmark = use_dm = False
    result = ValidationResult()
    for title, validator, data in (
        ("库存与门店清单", _validate_master, master_data),
        ("销售数据", _validate_sales, sales_data),
    ):
        try:
            validator(data, result, use_benchmark=use_benchmark, use_dm=use_dm,
                      existing_stock=existing_stock)
        except Exception as error:
            result.errors.append(f"{title}读取失败：{str(error)[:200]}")
    return result
