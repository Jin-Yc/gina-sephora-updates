"""Existing-stock demand, exact proportional targets and shared CDC dispatch."""
from dataclasses import dataclass
from fractions import Fraction
from typing import Callable, Mapping

import pandas as pd

from gina_sephora.domain.allocation import AllocationResult, dispatch_warehouses
from gina_sephora.data_io.validation import ValidationResult


DEMAND_FIELDS = ["Mini", "Sales qty", "Total Stock", "Order Quantity"]


@dataclass
class ExistingStockPlan:
    details: pd.DataFrame
    items: pd.DataFrame
    warehouses: list[str]


def automatic_ratio(stock: int, demand: int) -> Fraction:
    return min(Fraction(1), Fraction(stock, demand)) if demand > 0 else Fraction(1)


def manual_cap_percent(stock: int, demand: int) -> float:
    """Manual UI uses 0.01 percentage-point steps; never round its cap up."""
    value = automatic_ratio(stock, demand) * 10_000
    return (value.numerator // value.denominator) / 100


def demand_targets(demands: dict[str, int], ratio: Fraction) -> dict[str, int]:
    if ratio < 0 or ratio > 1 or any(value < 0 for value in demands.values()):
        raise ValueError("需求必须非负，分配比例必须在 0–100% 内。")
    quotas = {store: demand * ratio for store, demand in demands.items()}
    targets = {store: value.numerator // value.denominator for store, value in quotas.items()}
    total = sum(quotas.values(), Fraction())
    remaining = total.numerator // total.denominator - sum(targets.values())
    order = sorted(demands, key=lambda store: (-(quotas[store] - targets[store]), store))
    for store in order[:remaining]:
        targets[store] += 1
    return targets


def prepare_existing_stock(result: ValidationResult) -> ExistingStockPlan:
    if result.errors:
        raise ValueError("文件检查未通过，请修正文件后再计算需求。")
    sku, stores, sales = (result.tables[name] for name in ("SKU", "Store list", "销售数据"))
    required = {"Material", "Store", *DEMAND_FIELDS}
    if not required.issubset(sales.columns):
        raise ValueError("现货需求数据缺少 Mini、Sales qty、Total Stock 或 Order Quantity，请按现货模式重新检查。")
    if sales.duplicated(["Material", "Store"]).any():
        raise ValueError("现货数据的 Material + Store 必须唯一。")
    for field in DEMAND_FIELDS:
        values = pd.to_numeric(sales[field], errors="coerce")
        invalid = values.isna() | values.isin([float("inf"), float("-inf")]) | values.mod(1).ne(0)
        invalid |= sales[field].map(lambda value: isinstance(value, bool))
        if field != "Sales qty":
            invalid |= values.lt(0)
        if invalid.any():
            raise ValueError(f"现货 {field} 有无效数量，请重新检查文件。")
    names = {}
    if "Material Description" in sales:
        for material, name in sales[["Material", "Material Description"]].itertuples(index=False, name=None):
            if pd.notna(name) and str(name).strip():
                names.setdefault(material, str(name).strip())
    if "Description" in sku:
        for material, name in sku[["Material", "Description"]].itertuples(index=False, name=None):
            if pd.notna(name) and str(name).strip():
                names[material] = str(name).strip()
    grid = sku[["Material"]].merge(stores.sort_values("store", kind="stable"), how="cross")
    grid = grid.merge(sales[["Material", "Store", *DEMAND_FIELDS]],
                      left_on=["Material", "store"], right_on=["Material", "Store"],
                      how="left", validate="one_to_one", indicator=True)
    grid["商品名称"] = grid["Material"].map(names).fillna("未提供名称")
    grid["数据完整"] = grid["_merge"].eq("both")
    for field in DEMAND_FIELDS:
        grid[field] = grid[field].astype("Int64")
    grid["原始需求量"] = grid["Mini"] + grid["Sales qty"] - grid["Total Stock"] - grid["Order Quantity"]
    grid["需求量"] = grid["原始需求量"].clip(lower=0)
    # Keep the store-list identifier even when the demand record is missing.
    grid = grid.drop(columns=["Store", "_merge"]).rename(columns={"store": "Store", "cdc": "优先 CDC"})
    items = []
    for _, item in sku.iterrows():
        material = item["Material"]
        rows = grid.loc[grid["Material"].eq(material)]
        demand = int(rows["需求量"].sum())
        stock = sum(int(item[warehouse]) for warehouse in result.warehouses)
        entry = {"Material": material, "商品名称": names.get(material, "未提供名称"),
                 "总库存": stock, "需求量合计": demand, "自动比例": float(automatic_ratio(stock, demand)),
                 "需求记录门店数": int(rows["数据完整"].sum()),
                 "缺失记录门店数": int((~rows["数据完整"]).sum())}
        entry.update({warehouse: int(item[warehouse]) for warehouse in result.warehouses})
        items.append(entry)
    return ExistingStockPlan(grid, pd.DataFrame(items), list(result.warehouses))


def _ratio(stock: int, demand: int, override: float | None) -> Fraction:
    auto = automatic_ratio(stock, demand)
    if override is None:
        return auto
    try:
        if isinstance(override, bool):
            raise ValueError
        ratio = Fraction(str(override)) / 100
    except (ValueError, ZeroDivisionError):
        raise ValueError("手动比例必须是 0–100 范围内的有效数字。") from None
    if ratio < 0 or ratio > auto:
        raise ValueError(f"手动比例必须在 0–{float(auto * 100):.6f}% 内，不得超过自动比例。")
    return ratio


def calculate_existing_allocation(
    plan: ExistingStockPlan, *, percentages: Mapping[str, float] | None = None,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> AllocationResult:
    percentages = percentages or {}
    if set(percentages) - set(plan.items["Material"]):
        raise ValueError("手动比例包含不在当前 SKU 清单中的货号，请重新检查。")
    # Validate all overrides before publishing or dispatching any item.
    ratios = {item["Material"]: _ratio(int(item["总库存"]), int(item["需求量合计"]),
                                      percentages.get(item["Material"]))
              for _, item in plan.items.iterrows()}
    details, summaries, notices = [], [], []
    for number, (_, item) in enumerate(plan.items.iterrows(), start=1):
        material = item["Material"]
        rows = plan.details.loc[plan.details["Material"].eq(material)]
        ratio = ratios[material]
        stocks = {warehouse: int(item[warehouse]) for warehouse in plan.warehouses}
        demands = {row["Store"]: int(row["需求量"]) for _, row in rows.iterrows() if row["数据完整"]}
        targets = demand_targets(demands, ratio)
        preferred = dict(zip(rows["Store"], rows["优先 CDC"]))
        shipments, dispatch_error = {}, None
        try:
            shipments, dispatch_notices = dispatch_warehouses(targets, preferred, stocks)
            notices.extend(f"SKU {material}：{notice}" for notice in dispatch_notices)
        except ValueError as error:
            dispatch_error = str(error)
            notices.append(f"SKU {material}：{dispatch_error}")
        shipped_by_warehouse = {warehouse: 0 for warehouse in plan.warehouses}
        for _, source in rows.iterrows():
            store, complete = source["Store"], bool(source["数据完整"])
            target = targets.get(store, 0)
            demand = demands.get(store, 0)
            pending = bool(dispatch_error and target > 0)
            quantities = {warehouse: None if pending else shipments.get(store, {}).get(warehouse, 0)
                          for warehouse in plan.warehouses}
            actual = None if pending else sum(qty for qty in quantities.values() if qty is not None)
            donor = next((warehouse for warehouse, qty in quantities.items()
                          if warehouse != preferred[store] and qty is not None and qty > 0), "")
            if not complete:
                status = "需求数据缺失，暂不分配"
            elif pending:
                status = "CDC 出库待确认"
            elif demand == 0:
                status = "无补货需求"
            elif target == 0:
                status = "比例为 0，暂不分配" if ratio == 0 else "比例取整后为 0"
            else:
                status = "缺货" if actual is not None and actual < target else "跨仓调货" if donor else "正常分配"
            row = {"Material": material, "商品名称": item["商品名称"], "Store": store,
                   "curstatus": source["curstatus"], "storename": source["storename"], "优先 CDC": preferred[store],
                   "CDC 库存合计": int(item["总库存"]),
                   **{field: source[field] for field in DEMAND_FIELDS},
                   "原始需求量": source["原始需求量"], "需求量": demand if complete else None,
                   "自动比例": item["自动比例"], "实际使用比例": float(ratio),
                   "理论分货量": float(demand * ratio) if complete else None,
                   "allocation": target if complete else None, "比例缩减量": demand - target if complete else None,
                   "实际出库量": actual, "缺货量": target - actual if complete and actual is not None else None,
                   "未满足需求量": demand - actual if complete and actual is not None else None,
                   "调货 CDC": donor, "调货数量": None if pending else quantities.get(donor, 0),
                   "最终分配量": actual if complete else None, "状态": status,
                   **{f"{warehouse} 出库": qty for warehouse, qty in quantities.items()}}
            details.append(row)
            if not pending:
                for warehouse, quantity in quantities.items():
                    if quantity is not None:
                        shipped_by_warehouse[warehouse] += quantity
        shipped = sum(shipped_by_warehouse.values())
        target_total = sum(targets.values())
        missing = int(item["缺失记录门店数"])
        entry = {"Material": material, "商品名称": item["商品名称"], "Total QTY": int(item["总库存"]),
                 "需求量合计": int(item["需求量合计"]), "缺失记录门店数": missing,
                 "自动比例": item["自动比例"], "实际使用比例": float(ratio),
                 "比例来源": "手动" if material in percentages else "自动", "目标量合计": target_total,
                 "比例缩减量合计": int(item["需求量合计"]) - target_total,
                 "实际出库合计": None if dispatch_error else shipped,
                 "缺货量合计": None if dispatch_error else target_total - shipped,
                 "未满足需求量合计": None if dispatch_error else int(item["需求量合计"]) - shipped,
                 "剩余库存": None if dispatch_error else int(item["总库存"]) - shipped,
                 "状态": "出库待确认" if dispatch_error else "需求数据缺失，暂不分配" if not demands
                         else "存在缺货" if shipped < target_total
                         else "无补货需求" if not demands or not sum(demands.values()) else "分配完成"}
        if missing:
            if demands:
                entry["状态"] += "；部分需求数据缺失"
            notices.append(f"SKU {material} 有 {missing} 个门店缺少需求记录，已排除；需求和自动比例只包含记录完整的门店。")
        if not dispatch_error and shipped < target_total:
            notices.append(f"SKU {material} 受每店最多一个其他 CDC 的限制，有 {target_total - shipped} 件目标量无法出库。")
        entry.update({f"{warehouse} 剩余": None if dispatch_error else stocks[warehouse] - shipped_by_warehouse[warehouse]
                      for warehouse in plan.warehouses})
        summaries.append(entry)
        if on_progress:
            on_progress(number, len(plan.items), material)
    return AllocationResult(pd.DataFrame(details), pd.DataFrame(summaries), pd.DataFrame(), notices)
