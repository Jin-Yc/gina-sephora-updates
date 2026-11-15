"""Application shell: shared desktop controls and explicit page navigation."""
import os

import streamlit as st

from desktop_runtime import is_desktop, request_shutdown
from version import APP_VERSION


st.set_page_config(page_title=f"分货工具 v{APP_VERSION}", page_icon="📁", layout="wide")
page = st.navigation(
    [
        st.Page("app_pages/new_stock.py", title="新货分货", default=True,
                icon=":material/inventory_2:",
                help="沿用 v1.0 新货规则，上传文件、检查并计算分货，导出 CDC 明细。"),
        st.Page("app_pages/existing_stock.py", title="现货分货", url_path="existing_stock",
                icon=":material/warehouse:",
                help="按各店实际需求补货，查看 item 库存和需求，调整比例并导出 CDC 出库明细。"),
    ],
    position="sidebar",
)
st.sidebar.caption(f"分货工具 v{APP_VERSION} · 新货 / 现货")
if is_desktop() and os.environ.get("GINA_UPDATE_STATUS"):
    st.sidebar.caption(os.environ["GINA_UPDATE_STATUS"])
st.title("分货工具")
if is_desktop():
    with st.container(horizontal=True, horizontal_alignment="right"):
        if st.button("退出程序", key="exit_desktop"):
            st.info("程序正在退出，可以关闭此页面。")
            request_shutdown()
            st.stop()
    st.caption("关闭网页后程序仍在后台运行；使用本页“退出程序”可完全退出。")
st.subheader(page.title)
page.run()
