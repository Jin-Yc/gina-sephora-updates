"""Original per-SKU CDC stock stays visible in both allocation result files."""
from io import BytesIO
import unittest

from openpyxl import load_workbook

from gina_sephora.data_io.exporting import details_to_excel
from gina_sephora.data_io.validation import validate_uploads
from gina_sephora.domain.allocation import calculate_allocation
from gina_sephora.domain.existing_stock import calculate_existing_allocation, prepare_existing_stock
from tests.fixtures import existing_inputs
from tests.test_dm_inventory import fixture


class CDCStockExportTests(unittest.TestCase):
    def test_new_stock_uses_original_stock_before_dm_or_warehouse_limits(self):
        from gina_sephora.domain.adjustments import WarehouseLimit

        sku, stores, sales = fixture(stock=100)
        allocation = calculate_allocation(
            sku, stores, sales, ["CDC2", "CDC3"], use_dm=True,
            warehouse_limits={"CDC2": WarehouseLimit(0, 50)},
        )
        self.assertEqual(47, allocation.summary.iloc[0]["常规可分配库存"])
        self.assertTrue(allocation.details["CDC 库存合计"].eq(100).all())
        self.check_excel(allocation.details, {"A": 100})

    def test_existing_stock_includes_original_stock_for_each_sku_and_missing_store(self):
        result = validate_uploads(*existing_inputs(), existing_stock=True)
        allocation = calculate_existing_allocation(prepare_existing_stock(result), percentages={"A": 0, "B": 80})
        self.assertEqual([0, 8], allocation.summary["实际出库合计"].tolist())
        self.check_excel(allocation.details, {"A": 8, "B": 20})

    def check_excel(self, details, expected):
        book = load_workbook(BytesIO(details_to_excel(details)), read_only=True)
        sheet = book.active
        headers = [cell.value for cell in sheet[1]]
        self.assertEqual(1, headers.count("CDC 库存合计"))
        material, stock = headers.index("Material"), headers.index("CDC 库存合计")
        self.assertEqual(len(details) + 1, sheet.max_row)
        for row in sheet.iter_rows(min_row=2):
            self.assertEqual(expected[row[material].value], row[stock].value)
            self.assertEqual("#,##0", row[stock].number_format)
        book.close()


if __name__ == "__main__":
    unittest.main()
