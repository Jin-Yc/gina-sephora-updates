"""Inventory-backed DM demand and result columns; reserved warehouse limits."""
from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class WarehouseLimit:
    threshold: int
    percentage: int


def limit_warehouse_stocks(stocks: dict[str, int], limits=None) -> dict[str, int]:
    """Strictly above threshold, floor the percentage of the original stock."""
    effective = dict(stocks)
    for warehouse, limit in (limits or {}).items():
        if warehouse not in stocks:
            raise ValueError(f"库存限制指定了未知仓库：{warehouse}")
        if limit.threshold < 0 or int(limit.threshold) != limit.threshold:
            raise ValueError("库存阈值必须是非负整数。")
        if not 0 <= limit.percentage <= 100 or int(limit.percentage) != limit.percentage:
            raise ValueError("库存比例必须是 0 到 100 的整数百分比。")
        if stocks[warehouse] > limit.threshold:
            effective[warehouse] = stocks[warehouse] * limit.percentage // 100
    return effective


def _dm_values(frame, key, allowed):
    if "DM" not in frame:
        raise ValueError(f"启用 DM 时，{key} 必须包含 DM 列。")
    values = frame["DM"].map(lambda value: "0" if pd.isna(value) or str(value).strip() == "" else str(value).strip())
    numeric = pd.to_numeric(values, errors="coerce")
    if not numeric.isin(allowed).all():
        raise ValueError(f"{key} 的 DM 只能填写 {sorted(allowed)}，空白视为 0。")
    return dict(zip(frame[key], numeric.astype(int)))


def dm_requirements(sku, stores, *, enabled=False) -> dict[str, dict[str, int]]:
    """DM is a mandatory one-unit demand, reserved before normal SOB allocation."""
    if not enabled:
        return {material: {store: 0 for store in stores["store"]} for material in sku["Material"]}
    sku_dm = _dm_values(sku, "Material", {0, 251, 315})
    store_dm = _dm_values(stores, "store", {0, 1})
    return {material: {store: int(rule == 315 or (rule == 251 and store_dm[store] == 1))
                       for store in stores["store"]} for material, rule in sku_dm.items()}


def apply_dm_adjustment(details, summary, sku, stores, *, enabled=False):
    """Attach DM metadata after the shared inventory-backed dispatch, never add units."""
    details, summary = details.copy(), summary.copy()
    sku_dm = _dm_values(sku, "Material", {0, 251, 315}) if enabled else (
        dict(zip(sku["Material"], sku["DM"])) if "DM" in sku else {}
    )
    store_dm = _dm_values(stores, "store", {0, 1}) if enabled else (
        dict(zip(stores["store"], stores["DM"])) if "DM" in stores else {}
    )
    details["SKU DM"] = details["Material"].map(sku_dm).replace("", 0).fillna(0)
    details["门店 DM"] = details["Store"].map(store_dm).replace("", 0).fillna(0)
    # Retain the former column for existing exports, now explicitly actual DM shipments.
    details["DM 加量"] = details["DM 实际出库"]
    summary["SKU DM"] = summary["Material"].map(sku_dm).replace("", 0).fillna(0)
    summary["DM 加量合计"] = summary["DM 实际出库合计"]
    return details, summary
