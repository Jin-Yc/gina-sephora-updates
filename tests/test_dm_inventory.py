"""Inventory-backed DM priority, SKU pauses and shared CDC constraints."""
from io import BytesIO
import unittest
from unittest.mock import patch

from openpyxl import load_workbook
import pandas as pd

from gina_sephora.domain.adjustments import WarehouseLimit
from gina_sephora.domain.allocation import DMAllocationError, calculate_allocation, dispatch_warehouses
from gina_sephora.data_io.exporting import details_to_excel


def fixture(stock=100, dm=315):
    sku = pd.DataFrame([{"Material": "A", "Total QTY": stock, "CDC2": stock, "CDC3": 0, "DM": dm}])
    stores = pd.DataFrame([
        {"store": "001", "cdc": "CDC2", "storename": "店一", "curstatus": "open", "DM": 1},
        {"store": "002", "cdc": "CDC3", "storename": "店二", "curstatus": "open", "DM": 0},
        {"store": "003", "cdc": "CDC2", "storename": "新店", "curstatus": "open", "DM": 1},
    ])
    sales = pd.DataFrame([{"Material": "A", "Store": "001", "Sales qty": 3},
                          {"Material": "A", "Store": "002", "Sales qty": 1}])
    return sku, stores, sales


class DMInventoryTests(unittest.TestCase):
    def calculate(self, stock=100, dm=315, *, enabled=True, **kwargs):
        sku, stores, sales = fixture(stock, dm)
        return calculate_allocation(sku, stores, sales, ["CDC2", "CDC3"], use_dm=enabled, **kwargs)

    def assert_conservation(self, allocation):
        for _, summary in allocation.summary.iterrows():
            rows = allocation.details.loc[allocation.details["Material"].eq(summary["Material"])]
            self.assertEqual(summary["实际出库合计"], summary["常规实际出库合计"] + summary["DM 实际出库合计"])
            self.assertEqual(summary["Total QTY"], summary["实际出库合计"] + summary["剩余库存"])
            self.assertEqual(summary["实际出库合计"], rows["实际出库量"].sum())
            self.assertTrue((rows["实际出库量"] == rows["常规实际出库"] + rows["DM 实际出库"]).all())
            self.assertTrue((rows["实际出库量"] == rows[[c for c in rows if c.endswith(" 出库")]].sum(axis=1)).all())

    def test_dm_reserved_before_sob_and_new_store_keeps_marker(self):
        allocation = self.calculate()
        summary = allocation.summary.iloc[0]
        self.assertEqual(97, summary["常规可分配库存"])
        self.assertEqual(97, summary["常规实际出库合计"])
        self.assertEqual(3, summary["DM 需求合计"])
        self.assertEqual(3, summary["DM 实际出库合计"])
        self.assertEqual(100, summary["含 DM 目标量合计"])
        self.assertEqual(100, summary["最终分配量合计"])
        new = allocation.details.set_index("Store").loc["003"]
        self.assertEqual(-10, new["allocation"])
        self.assertEqual(-10, new["基础 allocation"])
        self.assertEqual(1, new["DM 实际出库"])
        self.assertEqual(1, new["实际出库量"])
        self.assertEqual(1, new["最终分配量"])
        self.assertEqual(0, new["常规实际出库"])
        self.assert_conservation(allocation)

    def test_251_only_flagged_stores_receive_dm(self):
        allocation = self.calculate(dm=251)
        rows = allocation.details.set_index("Store")
        self.assertEqual([1, 0, 1], rows["DM 实际出库"].tolist())
        self.assertEqual(98, allocation.summary.iloc[0]["常规可分配库存"])
        self.assert_conservation(allocation)

    def test_disabled_and_zero_rule_preserve_full_normal_stock(self):
        for dm, enabled in [(315, False), (0, True)]:
            with self.subTest(dm=dm, enabled=enabled):
                allocation = self.calculate(stock=10, dm=dm, enabled=enabled)
                self.assertEqual([8, 2, -10], allocation.details["allocation"].tolist())
                self.assertEqual(0, allocation.details["DM 实际出库"].sum())
                self.assertEqual(10, allocation.details["常规实际出库"].sum())
                self.assertEqual(10, allocation.details["最终分配量"].sum())
                self.assert_conservation(allocation)

    def test_insufficient_stock_pauses_sku_without_partial_dm(self):
        for stock in [0, 2]:
            allocation = self.calculate(stock=stock)
            summary = allocation.summary.iloc[0]
            self.assertIn("DM 库存不足", summary["状态"])
            self.assertEqual(0, summary["实际出库合计"])
            self.assertEqual(stock, summary["剩余库存"])
            self.assertEqual(3, summary["DM 需求合计"])
            self.assertEqual(0, summary["DM 实际出库合计"])
            self.assertTrue(allocation.details["最终分配量"].isna().all())
            self.assertTrue(allocation.details["缺货量"].isna().all())
            self.assert_conservation(allocation)

    def test_exact_dm_stock_only_fulfills_dm(self):
        allocation = self.calculate(stock=3)
        self.assertEqual(0, allocation.summary.iloc[0]["常规实际出库合计"])
        self.assertEqual(3, allocation.summary.iloc[0]["DM 实际出库合计"])
        self.assert_conservation(allocation)

    def test_no_effective_sales_fulfills_dm_only_and_keeps_remainder(self):
        sku, stores, sales = fixture()
        sales["Sales qty"] = 0
        allocation = calculate_allocation(sku, stores, sales, ["CDC2", "CDC3"], use_dm=True)
        self.assertEqual(3, allocation.summary.iloc[0]["实际出库合计"])
        self.assertEqual(97, allocation.summary.iloc[0]["剩余库存"])
        self.assertEqual(0, allocation.details["常规实际出库"].sum())
        self.assertTrue(allocation.details["DM 实际出库"].eq(1).all())
        self.assert_conservation(allocation)

    def test_zero_sales_store_is_still_eligible_and_negative_pauses_entire_sku(self):
        sku, stores, sales = fixture()
        sales.loc[sales["Store"].eq("002"), "Sales qty"] = 0
        allocation = calculate_allocation(sku, stores, sales, ["CDC2", "CDC3"], use_dm=True)
        zero = allocation.details.set_index("Store").loc["002"]
        self.assertEqual(0, zero["基础 allocation"])
        self.assertEqual(1, zero["实际出库量"])
        self.assert_conservation(allocation)
        sales.loc[sales["Store"].eq("002"), "Sales qty"] = -1
        with patch("gina_sephora.domain.allocation.dispatch_warehouses", side_effect=AssertionError("must not dispatch")):
            allocation = calculate_allocation(sku, stores, sales, ["CDC2", "CDC3"], use_dm=True)
        self.assertEqual("负销量待确认", allocation.summary.iloc[0]["状态"])
        self.assertEqual(100, allocation.summary.iloc[0]["剩余库存"])
        self.assertEqual(0, allocation.details["DM 实际出库"].sum())
        self.assertTrue(allocation.details["最终分配量"].isna().all())
        self.assert_conservation(allocation)

    def test_joint_dispatch_limits_other_cdcs_across_normal_and_dm(self):
        sku, stores, sales = fixture(stock=12)
        sku.loc[0, ["CDC2", "CDC3"]] = [4, 4]
        sku["CDC5"] = 4
        stores = stores.iloc[:1]
        sales = sales.iloc[:1]
        allocation = calculate_allocation(sku, stores, sales, ["CDC2", "CDC3", "CDC5"], use_dm=True)
        row = allocation.details.iloc[0]
        self.assertEqual(1, row["DM 实际出库"])
        self.assertEqual(8, row["实际出库量"])
        self.assertEqual(7, row["常规实际出库"])
        self.assertEqual(4, row["缺货量"])
        self.assertEqual(1, sum(row[f"{w} 出库"] > 0 for w in ["CDC3", "CDC5"]))
        self.assert_conservation(allocation)

    def test_minimum_dispatch_and_confirmed_cdc_infeasibility(self):
        shipments, _ = dispatch_warehouses({"001": 2, "002": 1}, {"001": "CDC2", "002": "CDC2"},
                                          {"CDC2": 2}, minimums={"002": 1})
        self.assertEqual(1, shipments["002"]["CDC2"])
        with self.assertRaises(DMAllocationError):
            dispatch_warehouses({"001": 2}, {"001": "CDC2"}, {"CDC2": 0, "CDC3": 1, "CDC5": 1},
                                minimums={"001": 2})
        with patch("gina_sephora.domain.allocation.dispatch_warehouses", side_effect=DMAllocationError("DM cannot be met")):
            allocation = self.calculate()
        self.assertIn("DM 出库受限", allocation.summary.iloc[0]["状态"])
        self.assertEqual(0, allocation.summary.iloc[0]["实际出库合计"])
        self.assertEqual(100, allocation.summary.iloc[0]["剩余库存"])
        self.assertTrue(allocation.details["最终分配量"].isna().all())
        self.assert_conservation(allocation)

    def test_unconfirmed_dispatch_has_no_claimed_dm_or_shortage(self):
        with patch("gina_sephora.domain.allocation.dispatch_warehouses", side_effect=ValueError("timeout")):
            allocation = self.calculate()
        self.assertEqual("出库待确认", allocation.summary.iloc[0]["状态"])
        self.assertTrue(allocation.details["DM 实际出库"].isna().all())
        self.assertTrue(allocation.details["最终分配量"].isna().all())
        self.assertTrue(allocation.summary["剩余库存"].isna().all())
        self.assertTrue(allocation.details["缺货量"].isna().all())

    def test_materials_sharing_benchmark_do_not_share_stock_or_dm(self):
        sku, stores, sales = fixture(stock=10)
        second = sku.iloc[0].copy()
        second["Material"] = "B"
        second[["Total QTY", "CDC2"]] = 2
        sku = pd.concat([sku, second.to_frame().T], ignore_index=True)
        sku["benchmark"] = "A"
        allocation = calculate_allocation(sku, stores, sales, ["CDC2", "CDC3"], use_dm=True, use_benchmark=True)
        summary = allocation.summary.set_index("Material")
        self.assertEqual(10, summary.loc["A", "实际出库合计"])
        self.assertEqual(0, summary.loc["B", "实际出库合计"])
        self.assertEqual(2, summary.loc["B", "剩余库存"])
        self.assert_conservation(allocation)

    def test_dm_uses_effective_stock_and_export_does_not_add_units(self):
        allocation = self.calculate(stock=10, warehouse_limits={"CDC2": WarehouseLimit(0, 20)})
        self.assertIn("DM 库存不足", allocation.summary.iloc[0]["状态"])
        self.assertEqual(10, allocation.summary.iloc[0]["剩余库存"])
        allocation = self.calculate(stock=10)
        book = load_workbook(BytesIO(details_to_excel(allocation.details)), read_only=True)
        rows = list(book.active.values)
        headers = rows[0]
        self.assertEqual(10, sum(row[headers.index("实际出库量")] for row in rows[1:]))
        self.assertEqual(10, sum(row[headers.index("最终分配量")] for row in rows[1:]))
        self.assertEqual(3, sum(row[headers.index("DM 实际出库")] for row in rows[1:]))
        book.close()


if __name__ == "__main__":
    unittest.main()
