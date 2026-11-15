"""Small in-memory existing-stock input workbooks; no user data."""
from io import BytesIO

from openpyxl import Workbook


def existing_inputs(*, items=None, records=None, stores=None, stock_header="Total Stock"):
    items = items if items is not None else [
        ["A", "商品 A", 8, 6, 2, 0], ["B", "商品 B", 20, 15, 5, 0],
    ]
    records = records if records is not None else [
        ["A", "001", 5, 3, 1, 1], ["A", "002", 4, 1, 0, 1],
        ["B", "001", 4, 2, 0, 0], ["B", "002", 3, 2, 1, 0],
    ]
    stores = stores if stores is not None else [("001", "CDC2"), ("002", "CDC3"), ("003", "CDC2")]
    book = Workbook()
    sku = book.active
    sku.title = "SKU"
    sku.append(["Material", "Description", "Total QTY", "CDC2", "CDC3", "CDC5"])
    for row in items:
        sku.append(row)
    sheet = book.create_sheet("Store list")
    sheet.append(["store", "curstatus", "storename", "cdc"])
    for store, cdc in stores:
        sheet.append([store, "open", "门店" + store, cdc])
    master = BytesIO()
    book.save(master)
    sales = "Material\tStore\tMini\tSales qty\t" + stock_header + "\tOrder Quantity\n"
    sales += "\n".join("\t".join(str(value) for value in row) for row in records)
    return master.getvalue(), sales.encode("utf-8")
