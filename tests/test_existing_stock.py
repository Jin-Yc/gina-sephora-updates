"""Confirmed replenishment rules, integer conservation, validation and UI limits."""
from fractions import Fraction
from io import BytesIO
from pathlib import Path
import unittest
from unittest.mock import patch

from openpyxl import load_workbook
import pandas as pd
from streamlit.testing.v1 import AppTest

from gina_sephora.domain.existing_stock import calculate_existing_allocation, demand_targets, prepare_existing_stock
from tests.fixtures import existing_inputs
from gina_sephora.data_io.validation import validate_uploads
from gina_sephora.services.workflows import EXISTING_STOCK, calculate_for_mode


ROOT = Path(__file__).resolve().parents[1]


class ExistingStockTests(unittest.TestCase):
    def plan(self, **kwargs):
        result = validate_uploads(*existing_inputs(**kwargs), existing_stock=True)
        self.assertFalse(result.errors, result.errors)
        return prepare_existing_stock(result)

    def test_demand_clamp_missing_zero_sales_and_negative_sales(self):
        plan = self.plan(records=[
            ["A", "001", 5, -2, 1, 1],  # Demand 1; negative sales participate.
            ["A", "002", 1, 0, 9, 0],   # Raw -8, demand 0.
            ["B", "001", 3, 0, 0, 0],   # Demand 3 despite zero sales.
        ])
        allocation = calculate_existing_allocation(plan)
        rows = allocation.details.set_index(["Material", "Store"])
        self.assertEqual(1, rows.loc[("A", "001"), "需求量"])
        self.assertEqual(-8, rows.loc[("A", "002"), "原始需求量"])
        self.assertEqual(0, rows.loc[("A", "002"), "allocation"])
        self.assertEqual(3, rows.loc[("B", "001"), "实际出库量"])
        self.assertTrue(pd.isna(rows.loc[("A", "003"), "allocation"]))
        self.assertTrue(pd.isna(rows.loc[("A", "003"), "需求量"]))
        self.assertEqual(0, rows.loc[("A", "003"), "实际出库量"])
        self.assertIn("缺失", rows.loc[("A", "003"), "状态"])
        self.assertNotIn("benchmark", allocation.details)
        self.assertNotIn("DM 加量", allocation.details)
        self.assertNotIn("SOB", allocation.details)

    def test_short_stock_manual_reduction_sufficient_stock_and_zero_percent(self):
        plan = self.plan()
        for percentages, expected in [(None, [8, 10]), ({"A": 70, "B": 80}, [7, 8]), ({"A": 0}, [0, 10])]:
            with self.subTest(percentages=percentages):
                allocation = calculate_existing_allocation(plan, percentages=percentages)
                summary = allocation.summary
                self.assertEqual(expected, summary["目标量合计"].tolist())
                self.assertEqual(expected, summary["实际出库合计"].tolist())
                self.assertTrue((summary["Total QTY"] == summary["实际出库合计"] + summary["剩余库存"]).all())
                self.assertTrue((summary["需求量合计"] == summary["比例缩减量合计"] + summary["目标量合计"]).all())
                self.assertTrue((allocation.details["allocation"].dropna() >= 0).all())
        allocation = calculate_existing_allocation(plan, percentages={"A": 70})
        self.assertEqual([4, 3], allocation.details.query("Material == 'A'")["allocation"].dropna().tolist())

    def test_negative_total_stock_enters_original_formula_and_export(self):
        for header in ("Total Stock", "Total Stcok"):
            with self.subTest(header=header):
                plan = self.plan(items=[["A", "A", 8, 6, 2, 0]], stock_header=header, records=[
                    ["A", "001", 5, 3, -2, 1],  # 5 + 3 - (-2) - 1 = 9.
                    ["A", "002", 4, 1, 0, 1],   # Demand 4; combined demand 13.
                ])
                self.assertEqual(13, plan.items.iloc[0]["需求量合计"])
                self.assertAlmostEqual(8 / 13, plan.items.iloc[0]["自动比例"])
                output = calculate_existing_allocation(plan)
                rows = output.details.set_index("Store")
                self.assertEqual(-2, rows.loc["001", "Total Stock"])
                self.assertEqual(9, rows.loc["001", "原始需求量"])
                self.assertEqual([6, 2], rows.loc[["001", "002"], "allocation"].tolist())
                self.assertTrue(pd.isna(rows.loc["003", "Total Stock"]))
                self.assertTrue(pd.isna(rows.loc["003", "最终分配量"]))
                manual = calculate_existing_allocation(plan, percentages={"A": 50})
                self.assertEqual(6, manual.summary.iloc[0]["实际出库合计"])
                self.assertEqual(2, manual.summary.iloc[0]["剩余库存"])
                from gina_sephora.data_io.exporting import details_to_excel
                book = load_workbook(BytesIO(details_to_excel(output.details)))
                try:
                    sheet = book.active
                    columns = [cell.value for cell in sheet[1]]
                    self.assertEqual(-2, sheet.cell(2, columns.index("Total Stock") + 1).value)
                    self.assertEqual(9, sheet.cell(2, columns.index("需求量") + 1).value)
                finally:
                    book.close()

    def test_total_stock_still_rejects_blank_fractional_and_nonfinite_values(self):
        for value in ("", " ", 1.5, -1.5, "nan", "inf", "-inf", "not a number"):
            with self.subTest(value=value):
                validation = validate_uploads(*existing_inputs(records=[["A", "001", 5, 2, value, 1]]),
                                              existing_stock=True)
                self.assertTrue(any("Total Stock" in error for error in validation.errors), validation.errors)

    def test_backend_signed_stock_guard_keeps_other_quantity_constraints(self):
        for field, value in (("Total Stock", -1.5), ("Total Stock", None), ("Total Stock", True),
                             ("Total Stock", float("inf")), ("Mini", -1), ("Order Quantity", -1)):
            validation = validate_uploads(*existing_inputs(), existing_stock=True)
            validation.tables["销售数据"][field] = validation.tables["销售数据"][field].astype(object)
            validation.tables["销售数据"].loc[2, field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                prepare_existing_stock(validation)

    def test_backend_rejects_overrides_above_auto_and_invalid_values(self):
        plan = self.plan()
        for value in [80.01, 100, -1, float("nan"), float("inf"), True]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                calculate_existing_allocation(plan, percentages={"A": value})
        with self.assertRaises(ValueError):
            calculate_existing_allocation(plan, percentages={"UNKNOWN": 10})

    def test_exact_fraction_and_largest_remainder_use_original_quotas(self):
        # At 50%, quotas 0.5, 1.5, 2.5: tie is resolved by store ID, not reweighted floor(total).
        self.assertEqual({"001": 1, "002": 1, "003": 2},
                         demand_targets({"001": 1, "002": 3, "003": 5}, Fraction(1, 2)))
        plan = self.plan(items=[["A", "A", 2, 2, 0, 0]],
                         records=[["A", "001", 1, 0, 0, 0], ["A", "002", 2, 0, 0, 0]])
        self.assertEqual(2, calculate_existing_allocation(plan).summary.iloc[0]["目标量合计"])
        # Automatic 2/3 remains exact; UI precision must not lose an available unit.
        for stock in range(0, 11):
            for first in range(0, 6):
                for second in range(0, 6):
                    total = first + second
                    ratio = min(Fraction(1), Fraction(stock, total)) if total else Fraction(1)
                    targets = demand_targets({"001": first, "002": second}, ratio)
                    self.assertEqual(min(stock, total), sum(targets.values()))
                    self.assertTrue(0 <= targets["001"] <= first)
                    self.assertTrue(0 <= targets["002"] <= second)

    def test_cdc_constraint_shortage_is_separate_from_ratio_reduction(self):
        plan = self.plan(items=[["A", "A", 12, 4, 4, 4]],
                         stores=[("001", "CDC2")], records=[["A", "001", 20, 0, 0, 0]])
        allocation = calculate_existing_allocation(plan)
        row = allocation.details.iloc[0]
        self.assertEqual(12, row["allocation"])
        self.assertEqual(8, row["实际出库量"])
        self.assertEqual(8, row["比例缩减量"])
        self.assertEqual(4, row["缺货量"])
        self.assertEqual(12, row["未满足需求量"])
        self.assertEqual(1, sum(row[f"{warehouse} 出库"] > 0 for warehouse in ["CDC3", "CDC5"]))
        self.assertEqual(4, allocation.summary.iloc[0]["剩余库存"])

    def test_pending_cdc_does_not_publish_false_shortages_or_final_quantities(self):
        with patch("gina_sephora.domain.existing_stock.dispatch_warehouses", side_effect=ValueError("test timeout")):
            allocation = calculate_existing_allocation(self.plan())
        self.assertTrue(allocation.summary["实际出库合计"].isna().all())
        self.assertTrue(allocation.summary["缺货量合计"].isna().all())
        rows = allocation.details.query("需求量 > 0")
        self.assertTrue(rows["最终分配量"].isna().all())
        self.assertTrue(rows["缺货量"].isna().all())

    def test_validation_strict_quantities_duplicates_alias_and_missing_columns(self):
        master, sales = existing_inputs()
        result = validate_uploads(master, sales, existing_stock=True, use_benchmark=True, use_dm=True)
        self.assertFalse(result.errors, result.errors)
        self.assertNotIn("benchmark", result.tables["SKU"])
        with patch("gina_sephora.domain.allocation.apply_dm_adjustment", side_effect=AssertionError("existing stock must not call DM")):
            output = calculate_for_mode(EXISTING_STOCK, result, use_benchmark=True, use_dm=True)
        self.assertNotIn("DM 加量", output.details)
        for field_index, bad_value in [(2, ""), (2, -1), (3, 1.5), (4, 1.5), (5, -1), (5, "nan")]:
            row = ["A", "001", 5, 2, 1, 1]
            row[field_index] = bad_value
            with self.subTest(field=field_index, value=bad_value):
                self.assertTrue(validate_uploads(*existing_inputs(records=[row]), existing_stock=True).errors)
        duplicate = ["A", "001", 5, 2, 1, 1]
        self.assertTrue(validate_uploads(*existing_inputs(records=[duplicate, duplicate]), existing_stock=True).errors)
        self.assertFalse(validate_uploads(*existing_inputs(stock_header="Total Stcok"), existing_stock=True).errors)
        self.assertTrue(validate_uploads(master, b"Material\tStore\tSales qty\nA\t001\t1", existing_stock=True).errors)

    def test_no_demand_no_stock_outside_records_and_export(self):
        plan = self.plan(items=[["A", "A", 0, 0, 0, 0]], records=[["A", "001", 5, 0, 0, 0]])
        allocation = calculate_existing_allocation(plan)
        self.assertEqual(0, allocation.summary.iloc[0]["目标量合计"])
        plan = self.plan(records=[["A", "001", 0, 0, 0, 0], ["A", "999", 999, 0, 0, 0]])
        allocation = calculate_existing_allocation(plan)
        self.assertEqual(0, allocation.summary["需求量合计"].sum())
        self.assertEqual(0, allocation.summary["实际出库合计"].sum())
        allocation = calculate_existing_allocation(self.plan(records=[["A", "999", 5, 0, 0, 0]]))
        self.assertTrue(allocation.summary["状态"].str.contains("需求数据缺失").all())
        self.assertFalse(allocation.summary["状态"].str.contains("无补货需求").any())


class ExistingStockUITests(unittest.TestCase):
    def setUp(self):
        self.app = AppTest.from_file(str(ROOT / "main.py"), default_timeout=30).run()
        self.app.switch_page("gina_sephora/app_pages/existing_stock.py").run()
        self.master, self.sales = existing_inputs()
        self.upload(self.master, self.sales)

    def upload(self, master, sales):
        self.app.file_uploader[0].upload("existing.xlsx", master)
        self.app.file_uploader[1].upload("existing.xls", sales).run()
        self.app.button(key="existing_stock_check").click().run()
        self.assertHealthy()
        self.state = self.app.session_state["existing_stock_page_state"]

    def assertHealthy(self):
        self.assertFalse(self.app.exception, [item.message for item in self.app.exception])

    def edit(self, rows):
        from gina_sephora.ui.existing_stock_controls import MANUAL_COLUMN
        editor = self.app.data_editor[0]
        assert editor.key is not None
        current = self.app.session_state[editor.key]
        edits = {str(index): dict(values) for index, values in current.get("edited_rows", {}).items()}
        for index, value in rows.items():
            edits.setdefault(str(index), {})[MANUAL_COLUMN] = value
        self.app.session_state[editor.key] = {"edited_rows": edits, "added_rows": [], "deleted_rows": []}
        self.app.run()
        self.assertHealthy()

    def calculate(self):
        self.app.button(key="existing_stock_calculate").click().run()
        self.assertHealthy()

    def test_negative_total_stock_upload_calculate_and_reject_blank_replacement(self):
        self.upload(*existing_inputs(items=[["A", "A", 8, 6, 2, 0]], records=[
            ["A", "001", 5, 3, -2, 1], ["A", "002", 4, 1, 0, 1],
        ]))
        self.assertFalse(self.state.validation.errors)
        self.assertEqual(13, self.state.plan.items.iloc[0]["需求量合计"])
        self.calculate()
        self.assertEqual(8, self.state.allocation.summary.iloc[0]["实际出库合计"])
        self.assertTrue(self.app.download_button)
        self.upload(*existing_inputs(records=[["A", "001", 5, 3, "", 1]]))
        self.assertTrue(any("Total Stock" in error for error in self.state.validation.errors))
        self.assertIsNone(self.state.plan)
        self.assertIsNone(self.state.allocation)
        self.assertFalse(self.app.download_button)

    def test_native_table_bulk_and_repeated_edits_cap_and_restore_automatic(self):
        from gina_sephora.ui.existing_stock_controls import MANUAL_COLUMN
        table = self.app.data_editor[0].value
        self.assertEqual(MANUAL_COLUMN, table.columns[-1])
        self.assertEqual(["A", "B"], table["Material"].tolist())
        self.assertEqual(["商品 A", "商品 B"], table["商品名称"].tolist())
        self.assertEqual([80.0, 100.0], table["自动比例"].tolist())
        self.assertTrue(table[MANUAL_COLUMN].isna().all())
        self.assertFalse(self.app.slider)
        self.edit({0: 70.0, 1: 72.5})
        self.assertEqual({"A": 70, "B": 72.5}, self.state.percentages)
        self.calculate()
        self.assertEqual([7, 7], self.state.allocation.summary["目标量合计"].tolist())
        self.edit({0: 60.0})
        self.assertEqual({"A": 60, "B": 72.5}, self.state.percentages)
        self.assertIsNone(self.state.allocation)
        self.assertIsNone(self.state.excel)
        self.assertIsNotNone(self.state.validation)
        self.assertIsNotNone(self.state.plan)
        self.assertFalse(self.app.download_button)
        self.edit({1: None})
        self.assertEqual({"A": 60}, self.state.percentages)
        self.calculate()
        self.assertEqual([6, 10], self.state.allocation.summary["目标量合计"].tolist())
        self.edit({0: 95.0})
        self.assertEqual({"A": 80}, self.state.percentages)
        self.assertEqual(80, self.app.data_editor[0].value.iloc[0][MANUAL_COLUMN])
        self.assertTrue(self.app.warning)
        self.assertIsNone(self.state.allocation)
        self.edit({1: 80.0})
        self.assertEqual({"A": 80, "B": 80}, self.state.percentages)
        self.edit({0: None})
        self.assertEqual({"B": 80}, self.state.percentages)
        self.calculate()
        self.assertEqual([8, 8], self.state.allocation.summary["目标量合计"].tolist())
        self.edit({0: 0.0})
        self.calculate()
        self.assertEqual([0, 8], self.state.allocation.summary["目标量合计"].tolist())

    def test_table_and_export_survive_navigation_then_file_change_resets(self):
        from gina_sephora.ui.existing_stock_controls import MANUAL_COLUMN
        self.edit({0: 70.0, 1: 80.0})
        self.calculate()
        book = load_workbook(BytesIO(self.state.excel), read_only=True)
        values = list(book.active.values)
        self.assertNotIn("DM 加量", values[0])
        self.assertIn("实际使用比例", values[0])
        self.assertEqual("001", values[1][values[0].index("Store")])
        self.assertTrue(any("缺失" in str(row[values[0].index("状态")]) for row in values[1:]))
        book.close()
        with patch("gina_sephora.ui.allocation_page.calculate_for_mode", side_effect=AssertionError("must not recalculate")):
            self.app.switch_page("gina_sephora/app_pages/new_stock.py").run()
            self.app.switch_page("gina_sephora/app_pages/existing_stock.py").run()
            self.assertHealthy()
        self.assertEqual([70.0, 80.0], self.app.data_editor[0].value[MANUAL_COLUMN].tolist())
        self.assertEqual(1, len(self.app.download_button))
        self.edit({0: None})
        self.assertEqual({"B": 80.0}, self.state.percentages)
        self.app.file_uploader[1].upload("updated.xls", self.sales).run()
        self.assertHealthy()
        self.assertIsNone(self.state.plan)
        self.assertIsNone(self.state.allocation)
        self.assertEqual({}, self.state.percentages)
        self.assertIsNone(self.state.percentage_table)
        self.assertFalse(self.app.download_button)
        self.app.button(key="existing_stock_check").click().run()
        self.assertHealthy()
        self.assertTrue(self.app.data_editor[0].value[MANUAL_COLUMN].isna().all())

    def test_fractional_auto_remains_exact_after_clearing_manual_value(self):
        self.upload(*existing_inputs(items=[["A", "A", 2, 2, 0, 0]],
                                     records=[["A", "001", 1, 0, 0, 0], ["A", "002", 2, 0, 0, 0]]))
        self.calculate()
        self.assertEqual(2, self.state.allocation.summary.iloc[0]["目标量合计"])
        self.edit({0: 80.0})
        self.assertEqual({"A": 66.66}, self.state.percentages)
        self.edit({0: None})
        self.assertEqual({}, self.state.percentages)
        self.calculate()
        self.assertEqual(2, self.state.allocation.summary.iloc[0]["目标量合计"])

    def test_store_details_and_zero_demand_input(self):
        key = f"existing_stock_details_{self.state.input_revision}"
        self.app.session_state[key] = True
        self.app.run()
        self.assertHealthy()
        self.assertEqual("A", self.app.selectbox[0].value)
        self.app.selectbox[0].select("B").run()
        self.assertHealthy()
        self.assertTrue(any("B 的门店需求明细" == frame.proto.alt for frame in self.app.dataframe))
        self.upload(*existing_inputs(records=[["A", "001", 0, 0, 0, 0]]))
        self.edit({0: 50.0})
        self.assertEqual({}, self.state.percentages)
        self.assertTrue(self.app.data_editor[0].value.iloc[:, -1].isna().all())


if __name__ == "__main__":
    unittest.main()
