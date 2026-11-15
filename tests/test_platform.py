"""v2.0 navigation, isolated state and v1.0 regression checks."""
from io import BytesIO
from pathlib import Path
import unittest
from unittest.mock import patch

from openpyxl import Workbook, load_workbook
from streamlit.testing.v1 import AppTest

from allocation import calculate_allocation
from validation import validate_uploads
from tests.fixtures import existing_inputs


ROOT = Path(__file__).resolve().parents[1]


def inputs():
    book = Workbook()
    sku = book.active
    sku.title = "SKU"
    sku.append(["Material", "benchmark", "Total QTY", "CDC2", "CDC3", "DM"])
    sku.append(["MAT", "REF", 10, 7, 3, 315])
    stores = book.create_sheet("Store list")
    stores.append(["store", "curstatus", "storename", "cdc", "DM"])
    for store, cdc in [("001", "CDC2"), ("002", "CDC3"), ("003", "CDC2")]:
        stores.append([store, "open", "门店" + store, cdc, 1])
    stream = BytesIO()
    book.save(stream)
    sales = "Material\tStore\tSales qty\nREF\t001\t3\nREF\t002\t1\nMAT\t001\t1\nMAT\t002\t3\n"
    return stream.getvalue(), sales.encode("utf-8")


class PlatformTests(unittest.TestCase):
    def setUp(self):
        self.master, self.sales = inputs()
        self.app = AppTest.from_file(str(ROOT / "main.py"), default_timeout=30).run()
        self.assertHealthy()

    def assertHealthy(self):
        self.assertFalse(self.app.exception, [item.message for item in self.app.exception])

    def upload(self, prefix):
        master, sales = existing_inputs() if prefix == "existing" else (self.master, self.sales)
        self.app.file_uploader[0].upload(f"{prefix}.xlsx", master)
        self.app.file_uploader[1].upload(f"{prefix}.xls", sales).run()
        self.assertHealthy()

    def switch(self, page):
        self.app.switch_page(f"app_pages/{page}.py").run()
        self.assertHealthy()

    def test_shared_layout_and_independent_options(self):
        upload_labels = [widget.label for widget in self.app.file_uploader]
        self.app.checkbox(key="new_stock_use_benchmark").check().run()
        self.app.checkbox(key="new_stock_use_dm").check().run()
        self.switch("existing_stock")
        self.assertEqual(upload_labels, [widget.label for widget in self.app.file_uploader])
        self.assertEqual(["existing_stock_dm_disabled"], [widget.key for widget in self.app.checkbox])
        self.assertFalse(self.app.checkbox(key="existing_stock_dm_disabled").value)
        self.assertTrue(self.app.checkbox(key="existing_stock_dm_disabled").disabled)
        self.switch("new_stock")
        self.assertTrue(self.app.checkbox(key="new_stock_use_benchmark").value)
        self.assertTrue(self.app.checkbox(key="new_stock_use_dm").value)

    def test_existing_stock_demand_then_separate_calculation(self):
        self.switch("existing_stock")
        self.upload("existing")
        with patch("ui.allocation_page.calculate_for_mode", side_effect=AssertionError("must not calculate")):
            self.app.button(key="existing_stock_check").click().run()
        self.assertHealthy()
        self.assertTrue(self.app.success)
        self.assertEqual(3, len(self.app.dataframe) - len(self.app.data_editor))
        self.assertFalse(self.app.download_button)
        self.assertFalse(self.app.button(key="existing_stock_calculate").disabled)
        rules = " ".join(item.value for item in self.app.markdown)
        self.assertNotIn("SOB × 该 SKU 各 CDC 库存合计", rules)
        state = self.app.session_state["existing_stock_page_state"]
        self.assertIsNone(state.allocation)
        self.assertIsNone(state.excel)
        self.assertIsNotNone(state.plan)
        self.assertEqual([10, 10], state.plan.items["需求量合计"].tolist())
        self.app.button(key="existing_stock_calculate").click().run()
        self.assertHealthy()
        self.assertEqual(1, len(self.app.download_button))
        self.assertEqual([8, 10], state.allocation.summary["目标量合计"].tolist())

    def test_files_results_and_export_survive_switch_without_recalculation(self):
        self.upload("new")
        self.app.checkbox(key="new_stock_use_benchmark").check().run()
        self.app.checkbox(key="new_stock_use_dm").check().run()
        self.app.button(key="new_stock_check").click().run()
        self.assertHealthy()
        state = self.app.session_state["new_stock_page_state"]
        self.assertIsNotNone(state.allocation)
        validation = validate_uploads(self.master, self.sales, use_benchmark=True, use_dm=True)
        expected = calculate_allocation(
            validation.tables["SKU"], validation.tables["Store list"], validation.tables["销售数据"],
            validation.warehouses, use_benchmark=True, use_dm=True,
        )
        self.assertTrue(state.allocation.details.equals(expected.details))
        self.assertEqual(10, state.allocation.details["实际出库量"].sum())
        self.assertEqual(10, state.allocation.details["最终分配量"].sum())
        self.assertEqual(7, state.allocation.details["常规实际出库"].sum())
        self.assertEqual(3, state.allocation.details["DM 实际出库"].sum())
        new_store = state.allocation.details.query("Store == '003'").iloc[0]
        self.assertEqual(-10, new_store["allocation"])
        book = load_workbook(BytesIO(state.excel), read_only=True)
        self.assertEqual(4, book.active.max_row)
        book.close()
        with patch("ui.allocation_page.calculate_for_mode", side_effect=AssertionError("must not recalculate")):
            self.switch("existing_stock")
            self.upload("existing")
            self.app.button(key="existing_stock_check").click().run()
            self.switch("new_stock")
        self.assertIs(state, self.app.session_state["new_stock_page_state"])
        self.assertEqual("new.xlsx", state.files["master"].name)
        self.assertEqual("existing.xlsx", self.app.session_state["existing_stock_page_state"].files["master"].name)
        self.assertEqual(1, len(self.app.download_button))
        self.assertIn("new.xlsx", " ".join(item.value for item in self.app.caption))

    def test_input_changes_clear_only_current_page_results(self):
        self.switch("existing_stock")
        self.upload("existing")
        self.app.button(key="existing_stock_check").click().run()
        self.switch("new_stock")
        self.upload("new")
        self.app.button(key="new_stock_check").click().run()
        self.app.checkbox(key="new_stock_use_dm").check().run()
        self.assertHealthy()
        state = self.app.session_state["new_stock_page_state"]
        self.assertIsNone(state.validation)
        self.assertIsNone(state.allocation)
        self.assertIsNone(state.excel)
        self.assertFalse(self.app.download_button)
        self.assertIsNotNone(self.app.session_state["existing_stock_page_state"].validation)
        self.app.button(key="new_stock_check").click().run()
        self.app.file_uploader[1].clear().run()
        self.assertHealthy()
        self.assertNotIn("sales", state.files)
        self.assertIn("master", state.files)
        self.assertIsNone(state.allocation)
        self.assertTrue(self.app.button(key="new_stock_check").disabled)
        self.assertFalse(self.app.download_button)
        self.app.file_uploader[1].upload("new.xls", self.sales).run()
        self.app.button(key="new_stock_check").click().run()
        self.app.file_uploader[1].upload("broken.xls", b"bad input").run()
        self.assertIsNone(state.validation)
        self.assertIsNone(state.allocation)
        self.app.button(key="new_stock_check").click().run()
        self.assertHealthy()
        self.assertTrue(self.app.error)
        self.assertFalse(self.app.download_button)
        self.app.button(key="new_stock_clear_uploads").click().run()
        self.assertHealthy()
        self.assertFalse(state.files)
        self.assertTrue(self.app.button(key="new_stock_check").disabled)
        self.assertIsNotNone(self.app.session_state["existing_stock_page_state"].validation)


if __name__ == "__main__":
    unittest.main()
