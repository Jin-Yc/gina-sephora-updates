"""SOB, exact integer targets, and warehouse dispatch; independent of the UI."""
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Callable

import numpy as np
import pandas as pd
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix

from gina_sephora.domain.adjustments import apply_dm_adjustment, dm_requirements, limit_warehouse_stocks


@dataclass
class AllocationResult:
    details: pd.DataFrame
    summary: pd.DataFrame
    new_stores: pd.DataFrame
    notices: list[str] = field(default_factory=list)


class DMAllocationError(ValueError):
    """Confirmed inability to satisfy all mandatory DM demand for a SKU."""


def integer_targets(weights: dict[str, Fraction], stock: int) -> dict[str, int]:
    """Largest remainders, with store ID as a deterministic tie breaker."""
    if stock < 0 or int(stock) != stock:
        raise ValueError("可分配库存必须为非负整数。")
    total = sum(weights.values(), Fraction())
    if total <= 0 or any(value < 0 for value in weights.values()):
        raise ValueError("计算整数分货量需要非负销量，且有效销量合计大于零。")
    quotas = {store: value * stock / total for store, value in weights.items()}
    targets = {store: value.numerator // value.denominator for store, value in quotas.items()}
    remaining = stock - sum(targets.values())
    order = sorted(
        (store for store, value in weights.items() if value > 0),
        key=lambda store: (-(quotas[store] - targets[store]), store),
    )
    for store in order[:remaining]:
        targets[store] += 1
    if sum(targets.values()) != stock:
        raise ValueError("整数分货量与库存合计不一致。")
    return targets


def dispatch_warehouses(
    targets: dict[str, int], preferred: dict[str, str], stocks: dict[str, int],
    *, time_limit: float = 10.0, minimums: dict[str, int] | None = None,
) -> tuple[dict[str, dict[str, int]], list[str]]:
    """Maximize fulfilled units, then preferred units, then balance final stock.

    Non-preferred shipment quantities are linked to binary warehouse choices;
    each store can choose at most one non-preferred warehouse. A global solution
    avoids reporting shortages caused only by an unfortunate greedy row order.
    """
    if any(value < 0 or int(value) != value for value in [*targets.values(), *stocks.values()]):
        raise ValueError("目标量和仓库库存必须为非负整数。")
    minimums = minimums or {}
    if any(value < 0 or int(value) != value or value > targets.get(store, 0)
           for store, value in minimums.items()):
        raise ValueError("门店最低出库量必须为非负整数，且不得超过目标量。")
    if sum(minimums.values()) > sum(stocks.values()):
        raise DMAllocationError("可分配库存不足以满足全部 DM 需求，该 SKU 暂停分配。")
    stores = sorted(store for store, qty in targets.items() if qty > 0)
    warehouses = sorted(stocks)
    if not stores:
        return {}, []
    if not warehouses or any(preferred[store] not in stocks for store in stores):
        raise ValueError("门店优先 CDC 必须在库存仓库列表中。")
    n, w = len(stores), len(warehouses)
    quantities = np.array([targets[store] for store in stores], dtype=float)
    capacities = np.array([stocks[warehouse] for warehouse in warehouses], dtype=float)
    if (quantities < 0).any() or (capacities < 0).any():
        raise ValueError("目标量和仓库库存不得为负。")
    choices = [(i, j) for i, store in enumerate(stores)
               for j, warehouse in enumerate(warehouses) if warehouse != preferred[store]]
    x_count = n * w
    variable_count = x_count + len(choices) + 2
    high, low = variable_count - 2, variable_count - 1
    upper = np.concatenate([
        np.minimum(quantities[:, None], capacities[None, :]).ravel(),
        np.ones(len(choices)), [max(capacities), max(capacities)],
    ])
    integrality = np.ones(variable_count)
    integrality[-2:] = 0
    row_ids, column_ids, coefficients, lower_rows, upper_rows = [], [], [], [], []

    def add_row(entries, lower=-np.inf, upper_bound=np.inf):
        row = len(lower_rows)
        for column, coefficient in entries:
            row_ids.append(row)
            column_ids.append(column)
            coefficients.append(coefficient)
        lower_rows.append(lower)
        upper_rows.append(upper_bound)

    for i in range(n):
        add_row([(i * w + j, 1) for j in range(w)],
                lower=minimums.get(stores[i], 0), upper_bound=quantities[i])
    for j in range(w):
        shipped = [(i * w + j, 1) for i in range(n)]
        add_row(shipped, upper_bound=capacities[j])
        add_row([*shipped, (high, 1)], lower=capacities[j])
        add_row([*shipped, (low, 1)], upper_bound=capacities[j])
    per_store_choices = [[] for _ in stores]
    for k, (i, j) in enumerate(choices, start=x_count):
        add_row([(i * w + j, 1), (k, -quantities[i])], upper_bound=0)
        per_store_choices[i].append((k, 1))
    for entries in per_store_choices:
        if entries:
            add_row(entries, upper_bound=1)

    matrix = coo_matrix((coefficients, (row_ids, column_ids)),
                        shape=(len(lower_rows), variable_count)).tocsc()
    constraints = [LinearConstraint(matrix, lower_rows, upper_rows)]
    bounds = Bounds(np.zeros(variable_count), upper)
    options = {"time_limit": time_limit, "mip_rel_gap": 0.0}
    total_objective = np.zeros(variable_count)
    total_objective[:x_count] = 1
    preferred_objective = np.zeros(variable_count)
    for i, store in enumerate(stores):
        if preferred[store] in warehouses:
            preferred_objective[i * w + warehouses.index(preferred[store])] = 1

    def solve(objective):
        return milp(c=objective, integrality=integrality, bounds=bounds,
                    constraints=constraints, options=options)

    first = solve(-total_objective)
    if first.status == 2 and any(minimums.values()):
        raise DMAllocationError("CDC 出库限制下无法满足全部 DM 需求，该 SKU 暂停分配。")
    if first.x is None or (not first.success and
                           round(total_objective @ first.x) != sum(targets.values())):
        raise ValueError("CDC 出库计算未完成，暂不能确认出库或缺货数量，请重试。")
    shipped_total = round(total_objective @ first.x)
    constraints.append(LinearConstraint(total_objective[None, :], shipped_total, shipped_total))
    candidate = first
    if preferred_objective.any():
        second = solve(-preferred_objective)
        preferred_bound = sum(min(stocks[warehouse], sum(
            targets[store] for store in stores if preferred[store] == warehouse
        )) for warehouse in warehouses)
        if second.x is None or (not second.success and
                                round(preferred_objective @ second.x) != preferred_bound):
            raise ValueError("优先 CDC 出库安排未完成，暂不发布该 SKU 的出库结果，请重试。")
        candidate = second
        preferred_total = round(preferred_objective @ second.x)
        constraints.append(LinearConstraint(preferred_objective[None, :],
                                             preferred_total, preferred_total))
    notices = []
    if shipped_total < sum(stocks.values()) and w > 1:
        balance_objective = np.zeros(variable_count)
        balance_objective[high], balance_objective[low] = 1, -1
        balanced = solve(balance_objective)
        if balanced.success:
            candidate = balanced
        else:
            notices.append("库存均衡优化未完成；当前方案已确认最大可出库量及优先 CDC 出库量。")

    shipments = np.rint(candidate.x[:x_count]).astype(np.int64).reshape(n, w)
    if (shipments < 0).any() or (shipments.sum(axis=0) > capacities).any():
        raise ValueError("CDC 出库结果校验失败：库存约束不满足。")
    if (shipments.sum(axis=1) > quantities).any() or shipments.sum() != shipped_total:
        raise ValueError("CDC 出库结果校验失败：目标数量约束不满足。")
    if any(shipments[i].sum() < minimums.get(store, 0) for i, store in enumerate(stores)):
        raise ValueError("CDC 出库结果校验失败：DM 最低出库量未满足。")
    for i, store in enumerate(stores):
        donors = [j for j, warehouse in enumerate(warehouses)
                  if warehouse != preferred[store] and shipments[i, j] > 0]
        if len(donors) > 1:
            raise ValueError("CDC 出库结果校验失败：一家店使用了多个其他仓库。")
    return {store: {warehouse: int(shipments[i, j])
                    for j, warehouse in enumerate(warehouses)}
            for i, store in enumerate(stores)}, notices


def calculate_allocation(
    sku: pd.DataFrame, stores: pd.DataFrame, sales: pd.DataFrame,
    warehouses: list[str],
    on_progress: Callable[[int, int, str], None] | None = None,
    *, use_benchmark: bool = False, use_dm: bool = False, warehouse_limits=None,
) -> AllocationResult:
    """Input tables must have passed validation.validate_uploads()."""
    if sku["Material"].duplicated().any() or stores["store"].duplicated().any():
        raise ValueError("SKU 货号和门店清单店号必须唯一，请先修正重复记录。")
    if use_benchmark and ("benchmark" not in sku or sku["benchmark"].isna().any()
                          or sku["benchmark"].astype(str).str.strip().eq("").any()):
        raise ValueError("已选择 benchmark 分货，每个 SKU 都必须填写 benchmark。")
    ordered_stores = stores.sort_values("store", kind="stable")
    store_ids = ordered_stores["store"].tolist()
    preferred = dict(zip(ordered_stores["store"], ordered_stores["cdc"]))
    store_names = dict(zip(ordered_stores["store"], ordered_stores["storename"]))
    store_statuses = dict(zip(ordered_stores["store"], ordered_stores["curstatus"]))
    known_stores = set(sales["Store"])
    new_ids = set(store_ids) - known_stores
    new_stores = ordered_stores.loc[ordered_stores["store"].isin(new_ids)].copy()
    source_column = "benchmark" if use_benchmark else "Material"
    relevant = sales.loc[sales["Store"].isin(store_ids) & sales["Material"].isin(sku[source_column])]
    weights_by_sku = {}
    duplicate_sales = relevant.duplicated(["Material", "Store"], keep=False).any()
    for material, store, value in relevant[["Material", "Store", "Sales qty"]].itertuples(index=False, name=None):
        weights = weights_by_sku.setdefault(material, {})
        weights[store] = weights.get(store, Fraction()) + Fraction(str(value))
    details, summary, notices = [], [], []
    if duplicate_sales:
        notices.append("同一 SKU、同一门店的多条销售记录已按销量相加用于计算 SOB，请确认原始记录没有重复导入。")

    dm_by_material = dm_requirements(sku, ordered_stores, enabled=use_dm)
    for number, (_, item) in enumerate(sku.iterrows(), start=1):
        material = item["Material"]
        sales_material = item[source_column]
        sku_label = f"SKU {material}（销量来源：{sales_material}）"
        original_stocks = {warehouse: int(item[warehouse]) for warehouse in warehouses}
        stocks = limit_warehouse_stocks(original_stocks, warehouse_limits)
        stock, original_stock = sum(stocks.values()), sum(original_stocks.values())
        dm_targets = dm_by_material[material]
        dm_total = sum(dm_targets.values())
        regular_stock = max(0, stock - dm_total)
        weights = {store: weights_by_sku.get(sales_material, {}).get(store, Fraction()) for store in store_ids}
        total = sum(weights.values(), Fraction())
        negative = any(value < 0 for value in weights.values())
        can_calculate = total > 0 and not negative
        paused = None
        if negative:
            paused = "负销量待确认"
            notices.append(f"{sku_label} 存在汇总后为负的门店销量，暂停整个 SKU（包括 DM），请先确认。")
        elif dm_total > stock:
            paused = "DM 库存不足，暂停分配"
            notices.append(f"{sku_label} 可分配库存 {stock:,}，DM 需求 {dm_total:,}，无法全部满足，整个 SKU 暂停分配。")
        elif total == 0:
            notices.append(f"{sku_label} 有效销量合计为 0，常规库存暂不分配。" +
                           ("DM 仍从库存优先分配。" if dm_total else ""))
        targets = integer_targets(weights, regular_stock) if can_calculate and not paused else {}
        combined = {store: targets.get(store, 0) + dm_targets[store] for store in store_ids}
        shipments, dispatch_error, dispatch_notices = {}, None, []
        if not paused and any(combined.values()):
            try:
                # Joint dispatch preserves the one-other-CDC limit across both purposes.
                shipments, dispatch_notices = dispatch_warehouses(
                    combined, preferred, stocks, minimums=dm_targets if dm_total else None,
                )
            except DMAllocationError as error:
                paused = "DM 出库受限，暂停分配"
                notices.append(f"{sku_label}：{error}")
            except ValueError as error:
                dispatch_error = str(error)
                notices.append(f"{sku_label}：{dispatch_error}")
        notices.extend(f"{sku_label}：{notice}" for notice in dispatch_notices)
        actual_by_warehouse = {warehouse: 0 for warehouse in warehouses}
        regular_shipped, dm_shipped = 0, 0
        for store in store_ids:
            new, dm_demand = store in new_ids, dm_targets[store]
            base_target = targets.get(store) if can_calculate and not paused else None
            planned = combined[store]
            base_allocation = -10 if new else base_target
            if new:
                target = -10
            elif not paused and (can_calculate or dm_demand):
                target = (base_target or 0) + dm_demand
            else:
                target = None
            pending = bool(dispatch_error and planned > 0)
            warehouse_qty = {warehouse: None if pending else shipments.get(store, {}).get(warehouse, 0)
                             for warehouse in warehouses}
            actual = None if pending else sum(value for value in warehouse_qty.values() if value is not None)
            actual_dm = None if pending and dm_demand else (0 if paused else dm_demand)
            normal_actual = actual - actual_dm if actual is not None and actual_dm is not None else None
            if paused:
                status = paused
            elif pending:
                status = "CDC 出库待确认"
            elif new:
                status = "新店／无历史销售记录"
            elif not can_calculate:
                status = "无有效销量，常规库存暂不分配" if dm_demand else "无有效销量，暂不分配"
            elif planned == 0:
                status = ("旧店 benchmark 无销量" if use_benchmark else "旧店该 SKU 无销量") if weights[store] == 0 else "取整后为 0"
            else:
                status = "缺货" if actual is not None and actual < planned else "正常分配"
            donor = next((warehouse for warehouse, quantity in warehouse_qty.items()
                          if warehouse != preferred[store] and quantity is not None and quantity > 0), "")
            base_status = status
            if donor and status == "正常分配":
                status = "跨仓调货"
            if actual_dm and not paused:
                status += f"；DM 已出库 {actual_dm}"
            shortage = planned - actual if actual is not None and not paused and (new or can_calculate or dm_demand) else None
            for warehouse, quantity in warehouse_qty.items():
                if quantity is not None:
                    actual_by_warehouse[warehouse] += quantity
            if normal_actual is not None:
                regular_shipped += normal_actual
            if actual_dm is not None:
                dm_shipped += actual_dm
            row = {
                "Material": material, "benchmark": item.get("benchmark", ""),
                "销量来源货号": sales_material, "分货依据": source_column,
                "Store": store, "curstatus": store_statuses[store],
                "storename": store_names[store], "优先 CDC": preferred[store],
                "CDC 库存合计": original_stock,
                "Sales qty": float(weights[store]), "有效门店总销量": float(total),
                "SOB": 0.0 if new else float(weights[store] / total) if can_calculate else None,
                "理论分货量": 0.0 if new else float(weights[store] * regular_stock / total) if can_calculate and not paused else None,
                "基础 allocation": base_allocation, "allocation": target,
                "DM 需求": dm_demand, "常规实际出库": normal_actual, "DM 实际出库": actual_dm,
                "实际出库量": actual, "缺货量": shortage,
                "调货 CDC": donor, "调货数量": None if pending else warehouse_qty.get(donor, 0),
                "基础状态": base_status, "状态": status,
                "最终分配量": None if paused or pending else actual,
            }
            row.update({f"{warehouse} 出库": quantity for warehouse, quantity in warehouse_qty.items()})
            details.append(row)
        shipped = sum(actual_by_warehouse.values())
        combined_total = sum(combined.values())
        # Paused SKUs have confirmed zero shipments and retain all stock. Pending
        # optimization results remain blank; neither state publishes partial DM.
        entry = {
            "Material": material, "benchmark": item.get("benchmark", ""),
            "销量来源货号": sales_material, "分货依据": source_column,
            "Total QTY": original_stock, "可分配库存": stock,
            "限制预留库存": original_stock - stock, "常规可分配库存": None if paused else regular_stock,
            "有效门店总销量": float(total), "SOB 合计": 1.0 if can_calculate else None,
            "目标量合计（不含 -10）": sum(targets.values()) if can_calculate and not paused else None,
            "DM 需求合计": dm_total, "含 DM 目标量合计": None if paused else combined_total,
            "常规实际出库合计": None if dispatch_error else regular_shipped,
            "DM 实际出库合计": None if dispatch_error else dm_shipped,
            "实际出库合计": None if dispatch_error else shipped,
            "缺货量合计": None if dispatch_error or paused or not (can_calculate or dm_total) else combined_total - shipped,
            "剩余库存": None if dispatch_error else original_stock - shipped,
            "最终分配量合计": None if dispatch_error or paused else shipped,
            "状态": paused or ("出库待确认" if dispatch_error else
                              "存在缺货" if shipped < combined_total else
                              "无有效销量，常规库存暂不分配" if not can_calculate and dm_total else
                              "无有效销量，暂不分配" if not can_calculate else "分配完成"),
        }
        entry.update({f"{warehouse} 剩余": None if dispatch_error else original_stocks[warehouse] - actual_by_warehouse[warehouse]
                      for warehouse in warehouses})
        summary.append(entry)
        if not paused and not dispatch_error and shipped < combined_total:
            notices.append(f"{sku_label} 已保障 DM，受每店最多一个其他 CDC 限制，仍有 {combined_total - shipped} 件常规目标未满足。")
        if on_progress:
            on_progress(number, len(sku), material)
    details, summary = apply_dm_adjustment(
        pd.DataFrame(details), pd.DataFrame(summary), sku, stores, enabled=use_dm,
    )
    return AllocationResult(details, summary, new_stores, notices)
