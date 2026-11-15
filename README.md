# 分货工具（支持桌面自动更新）

平台现在有左侧“新货分货”和“现货分货”两个页面，默认进入新货页。
新货按 SOB 分配常规库存，开启 DM 时先从库存保障 DM，再用剩余库存分货；现货按门店实际需求计算补货，支持各 item 比例调节、CDC 出库和 Excel 导出。
两个页面共用界面布局，独立保留上传文件和本页结果；新货保留 benchmark/DM，现货不使用 benchmark，DM 禁用。

- [项目结构与维护约定](docs/项目结构.md)
- [完整新货与现货分货规则](docs/分货规则.md)
- [变更记录](docs/变更记录.md)
- [本轮验收记录](docs/验收记录.md)
- [自动更新与维护者发布指南](docs/自动更新.md)
- [Git 源码管理与同步](docs/Git管理.md)

## v2.0 使用说明

运行源码后，通过左侧切换页面。新货页上传两份文件后点击“检查并计算分货”；现货页先点击“检查文件并计算需求”，在原生表格中查看所有 item 的名称、各 CDC 库存和需求，仅最后一列“手动比例（%）”可编辑；填写后点击“计算现货分货”并导出。
页面表格统一使用整数千分位、小数两位、百分比两位；比例表的门店记录合并显示为“完整 / 缺失”（例如 2 / 1）。显示精度不影响实际计算。
切换回来时，上传框下方“当前使用”显示本页保留的文件；重新上传可以替换，也可以点击“清除本页上传文件”。
更换文件会清除本页检查、手动比例和旧结果，需要重新检查；现货只修改比例时保留需求检查，需重新计算出库。新货 benchmark 或 DM 变更后需重新检查。关闭会话或重启程序后重新上传。
现货需求=max(0, Mini+Sales qty−Total Stock−Order Quantity)。自动按库存/需求比例分配；每个 item 可手动降低比例，表格中填 70 表示 70%，填 0 暂不分配，留空使用完整自动比例。输入范围为 0–100，超过该行自动上限会回退并提示；支持连续编辑和批量粘贴。
现货销售文件必须包含四个公式字段，并按 Material+Store 唯一匹配。原新货样例的 Material 与销售文件不匹配，直接用于现货会标记需求缺失，需上传对应 item 的现货数据。

新版桌面启动器支持启动时自动检查并安装业务代码更新。历史 v1.0 exe 没有此功能，需要首次换成新版。

## 用户发布版

只需分发 `dist/GinaSephora.exe` 这一个文件，无需附带 Python、虚拟环境、源码或依赖目录。
用户双击后会自动打开默认浏览器，不显示终端或额外启动窗口。首次启动需要数秒解压内置运行环境。
用户仍需自行选择作为业务输入的两份 Excel 文件。

用户每次完全退出后重新双击 exe，会从固定 GitHub Releases 地址检查更新；有兼容新版就自动下载、校验并运行，不需要用户手动下载或替换 exe。断网或更新失败继续使用本机版本，侧栏显示版本和检查状态。普通业务更新只发布小型代码包；Python、第三方依赖或固定启动器变化时仍需重建 exe。维护者操作见 [自动更新指南](docs/自动更新.md)。

发布版强制使用生产模式，关闭文件监听、自动重跑、开发工具栏和开发 Agent API，仅监听本机回环地址。
端口自动选择；重复双击会打开已经运行的实例，不重复启动后台服务。
关闭浏览器标签不会停止程序，使用网页顶部的“退出程序”完全退出。
启动异常会弹出提示，诊断日志保存在系统临时目录的 `GinaSephoraDesktop` 文件夹。

已验证：单文件复制到独立目录、移除 Python/Conda 搜索路径后，可读取样例、执行分货、导出 Excel、启动网页及退出。
验证范围为当前 Windows 11 电脑上的隔离测试，尚未在另一台干净电脑或虚拟机上验收。

## 启动

在项目文件夹打开 PowerShell，执行：

```powershell
.\.venv\Scripts\python.exe -m streamlit run main.py --server.address=127.0.0.1 --browser.gatherUsageStats=false
```

浏览器打开页面后，分别上传库存与门店清单、门店销售数据，再点击“检查并计算分货”。
需要参考其他货号的历史销量时，勾选“使用 benchmark 销量分货”。不勾选则按 Material 自身的销量计算。
需要 DM 陈列分货时，勾选“启用 DM 分配”。DM 优先从 CDC 库存出库，剩余库存按 SOB 分货；DM 无法全部满足时暂停该 SKU。
计算完成后，在明细下方点击“导出门店分货与 CDC 明细（Excel）”，保存完整结果。
在终端按 Ctrl+C 停止程序。
IDE 的 Python 解释器选择 `.venv\Scripts\python.exe`，无需手动激活环境。

## 项目文件

主要源码集中在 `gina_sephora/`：`domain/` 放分货和补货规则，`data_io/` 放输入校验与 Excel 导出，`services/` 串联业务流程，`ui/` 放共用界面与会话状态，`app_pages/` 放两个页面入口。
根目录的 `main.py` 和 `launcher.py` 提供网页、桌面启动入口；固定更新器及版本配置保留在根目录。完整职责、导入约定和兼容发布方式见 [项目结构](docs/项目结构.md)。
`docs/` 集中维护规则、结构、变更和验收记录。

## 基础检查

- 库存文件必须是真正的 Excel，包含 SKU、Store list 工作表。
- SKU 表需要 Material、Total QTY 和至少一个 CDC数字 仓库列，例如 CDC2。
- benchmark 分货模式还需要 benchmark 列（兼容 SOB benchmark 表头），每行都必须填写。
  不勾选时 benchmark 列可以没有或留空。多个不同 Material 可以共用一个 benchmark。
- Store list 表需要 store、curstatus、storename、cdc；销售数据需要 Material、Store、Sales qty。
- DM 开启时 SKU 与 Store list 都需要 DM 列：SKU 允许 315、251、0，门店允许 1、0，空白均视为 0。
  DM 关闭时可以不提供 DM 列，DM 内容不参与规则校验。
- 工作表名、列名忽略大小写和首尾空白，额外字段不会影响检查。
- 检查关键字段空白、重复货号或店号、数字有效性、非负整数库存。
- 检查 Total QTY 是否等于各仓库库存之和，门店优先仓库是否有对应库存列。
- 重复的货号与店号销售组合、负数或非整数销量、清单范围外销售记录只提示，不自动调整。
- 忽略整行空白，不把缺失的数量自动补成 0。原始数据行号显示在错误信息中。
- 销售文件支持真实 Excel，以及系统导出的 UTF-16/UTF-8/GB18030 制表符文本（可使用 .xls 后缀）。
- 数字形式的货号和店号会去除 Excel 的 .0 尾缀，文本中的前导零保留。

## 分货规则（新货）

- 每个 SKU 单独计算，只使用当前 Store list 范围内的销量作为分母，关闭门店不参与。
- 销量来源可选择 Material 或 benchmark，汇总及明细会显示实际使用的“销量来源货号”和“分货依据”。
- benchmark 仅决定销售占比；目标库存、CDC 出库和剩余库存始终按每个 Material 单独计算。
- 同一 benchmark 被多行 Material 引用时，每行使用相同的销售占比，但各自使用自己的库存。
- benchmark 缺失时不自动回退 Material；benchmark 没有有效销售时标记暂不分配。
- 当前店在整个销售文件中都不存在时，标记无历史记录，SOB=0、allocation=-10；常规出库为 0，适用 DM 时仍可从库存实际出库。
- 旧店仅缺少当前销量来源货号的销量时，销量按 0 计算；该货号总有效销量为正时，该店 SOB 与 allocation=0。
- 某 SKU 的总有效销量为 0，暂不分配。负销量尚未约定处理口径，若同 SKU 同店汇总后为负，则暂停该 SKU。
- 同一 SKU 同一门店的多条销售记录相加；仍保留重复记录提示，便于用户确认并非误导入。
- 常规目标按扣除 DM 需求后的库存先向下取整，再按小数余数降序补齐；并列按店号文本升序。含 DM 目标合计不超过可分配库存。
- 先最大化可满足的目标数量，再最大化优先 CDC 出库量，最后最小化各 CDC 最终剩余库存的最大差值。
- 每店最多使用优先 CDC 和一个其他 CDC，任何 CDC 都不能超出当前 SKU 可分配库存。
- `allocation` 是目标量；`实际出库量` 和各 CDC 出库列是实际安排；差额列为缺货量。
- 无历史销售的 -10 只作为标记，不参与目标、实际出库、缺货或库存合计。
- 出库安排未完成最优性确认时，显示“出库待确认”，不把未确定的差额当作真实缺货。
- 每一阶段 CDC 优化最长计算 10 秒；若仅库存均衡阶段超时，保留已确认目标满足量及优先 CDC 数量的方案并提示。

## DM 与库存限制（新货）

- SKU DM=315：当前全部门店各 1 件；DM=251：仅门店 DM=1 各 1 件。按各 Material 独立计算，benchmark 不共享库存。
- 先保障 DM，再用剩余可分配库存按 SOB 计算常规目标；DM 与常规共同遵守优先 CDC＋最多一个其他 CDC。
- 库存不足或 CDC 限制使 DM 无法全部满足时，整个 SKU 暂停，库存保留，不发布部分 DM 或常规出库。
- 新店及零销量店仍适用 DM，新店 allocation 保留 -10，DM 需求和实际出库单独显示。
- 无有效销量可只分 DM；负销量暂停整个 SKU，包括 DM。出库计算待确认时不发布未经确认的数量。
- 实际出库量=常规实际出库+DM 实际出库=最终分配量。各 CDC 出库包含 DM，不再重复加量；总库存=实际出库合计+剩余库存。
- 兼容列“DM 加量”表示已经包含在实际出库中的 DM 实际数量，不可再次相加。
- 页面已预留 CDC2/CDC3/CDC5 的阈值及比例，所有控件 disabled=True，主流程不会启用限制。
- 后端预留 warehouse_limits 参数：库存严格大于阈值时，可分配量=原库存×百分比并向下取整；否则按全量。
  例如库存 2001、阈值 2000、比例 80%，可分配 1600；等于阈值不触发。预留部分仍计入剩余库存。

## 结果查看与导出

- SKU 汇总用于按货号核对总库存、目标量、实际出库、缺货与剩余库存，并查看未分配或待确认状态。
- 门店分货与 CDC 明细是具体执行清单，支持导出全部行，包含销量来源、SOB、目标量、实际出库、各仓库数量及状态。
- 明细与 Excel 导出中，Store 后面依次为 curstatus、storename，均按当前门店清单匹配。
- Excel 中货号和店号保留文本及前导零，SOB 显示为百分比，新店标记 -10 保留；未确定的数量保留空白。
- 导出只包含明细，不额外加入 SKU 汇总工作表。点击下载不会重新执行分货或清空当前结果。

在其他开发电脑重建环境时，使用 Python 3.13：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 重建发布包

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe -m ty check main.py gina_sephora tests packaging desktop_runtime.py launcher.py auto_update.py release_layout.py version.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
powershell -ExecutionPolicy Bypass -File .\build.ps1
.\.venv\Scripts\python.exe -u packaging\verify_release.py
```

重新构建后的发布文件为 `dist/GinaSephora.exe`；`build` 是构建及隔离验证的中间目录，无需交付给使用者。

日常修改业务代码后，提高 `version.py` 的版本号并运行 `publish-update.ps1` 生成代码更新包；已登录 GitHub CLI 时可加 `-Publish` 自动发布。无需每次重建或重新分发 exe。
