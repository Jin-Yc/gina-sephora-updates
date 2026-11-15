"""Mode configuration and the single entry point for allocation workflows."""
from dataclasses import dataclass
from typing import Callable

from gina_sephora.domain.allocation import AllocationResult, calculate_allocation
from gina_sephora.domain.existing_stock import ExistingStockPlan, calculate_existing_allocation, prepare_existing_stock
from gina_sephora.data_io.validation import ValidationResult


@dataclass(frozen=True)
class AllocationMode:
    id: str
    title: str
    can_calculate: bool


NEW_STOCK = AllocationMode("new_stock", "新货分货", True)
EXISTING_STOCK = AllocationMode("existing_stock", "现货分货", True)


def calculate_for_mode(
    mode: AllocationMode, result: ValidationResult, *,
    use_benchmark: bool = False, use_dm: bool = False,
    on_progress: Callable[[int, int, str], None] | None = None,
    percentages: dict[str, float] | None = None,
    existing_plan: ExistingStockPlan | None = None,
) -> AllocationResult:
    """Route demand-based replenishment separately from v1.0 SOB allocation."""
    if result.errors:
        raise ValueError("文件检查未通过，请修正文件后再计算。")
    if mode == EXISTING_STOCK:
        return calculate_existing_allocation(
            existing_plan if existing_plan is not None else prepare_existing_stock(result),
            percentages=percentages, on_progress=on_progress,
        )
    if mode != NEW_STOCK:
        raise ValueError("不支持的分货模式。")
    return calculate_allocation(
        result.tables["SKU"], result.tables["Store list"], result.tables["销售数据"],
        result.warehouses, use_benchmark=use_benchmark, use_dm=use_dm,
        on_progress=on_progress,
    )
