# gunstore-pos-mcp — POS 的 MCP 服务器(Python/uv)

gunstore-pos 平台的 MCP server 源码仓(114 工具 = 10 个通用 Frappe CRUD + 56 个业务工具 + 12 个分销商工具 + 7 个 CPA 报表工具 + 29 个门店运营工具:寄售出库/订单履约/4473/RSR/FastBound/Woo/GunBroker/库存/FFL/财税报表/盘点·现金抽屉·库位;**默认注册 107 个**——4 个分销商队列动作需 `GUNSTORE_MCP_DISTRIBUTOR_ACTIONS=1`、3 个 GunBroker 写动作需 `GUNSTORE_MCP_GUNBROKER_ACTIONS=1` 显式开启,否则物理不注册)。工具清单与语义见 `TOOLS.md`。gunstore-pos 仓的 `.mcp.json` 以 `uv run --directory <本仓> gunstore-mcp` 方式引用。

**模式**:`GUNSTORE_MCP_MODE=cpa` 启动只读会计面(恰 25 工具,写面物理不注册 + client 层方法 allowlist + Settings 读 blocklist 三层防御,见 `gunstore_mcp/modes.py`);默认 `full` 全量。未知模式值拒绝启动(fail-closed)。

**传输**:`GUNSTORE_MCP_TRANSPORT=stdio`(默认,本机 API key)| `http`(远程连接器:POS 当 OAuth 授权服务器,本服务器只验并转发用户自己的 bearer,**不持有任何 key**;`upload_attachment` 远程物理不注册;未知值拒启)。见 README「Remote connector」与 `gunstore_mcp/auth.py`。

**两个动作闸**(互相独立,也都独立于 `GUNSTORE_MCP_MODE`;开任何一个都**不会**给 cpa 面加工具):
- `GUNSTORE_MCP_DISTRIBUTOR_ACTIONS=1` → 那 4 个分销商动作(confirm/cancel/reroute/update_order_ffl)
- `GUNSTORE_MCP_GUNBROKER_ACTIONS=1` → `gb_push_serial` / `gb_end_listing` / `gb_pull_orders`(**只有写的那三个**;`gb_test_connection` / `gb_listing_status` 永远注册——查看和探活正是你希望人在动手前先做的事)。`gb_pull_orders` 只是"拉订单"却也在闸内:导进来一张订单会建 POS 单据并**预留那把枪**

默认都**物理不注册**——与 cpa 模式同一姿态:"存在但拒绝"挡不住"agent 自认为该确认",而用户级实例真的指向 prod。此处不像 mode 那样对未知值拒绝启动:任何非显式真值都=关,方向天然 fail-closed。

**`gb_push_serial` 为什么够格进这个闸**:它把一把真枪挂上公开拍卖行,买家可以在任何人发现之前拍下,撤下来要人去 GunBroker 站点手动做。`gb_end_listing` 是它的配对项——两者属同一个操作员决定,拆开会造成"能结束不能重挂"。**`gb_pull_orders` 名字像只读,判据看的是后果**:导进来一张订单会建 POS 单据、按序列号匹配库存并**预留那把枪**;打错环境或有人把水位(`orders_since_override`)往回拨过,这一轮就会预留一批没人买过的枪。

**部署前置(lead 裁决,G5 清单)**:要用 GunBroker 渠道的实例**必须**设 `GUNSTORE_MCP_GUNBROKER_ACTIONS=1`。只开上架不开结束是最危险的配置——柜台卖掉后没人能结束 listing,而自动结束链路(`serial_channel_exit`)要到 PR-2/PR-3 才上线。

**门店运营工具(`tools/shopfloor.py`,POS 1.5.0-beta.15 的盘点 / 现金抽屉 / 库位)**:读 12 + 写 17。**每个写都要 `confirm=true`(owner 拍板:全部开放含钱和库存),但没有注册期开关**——这是和上面两个闸的有意区别:GunBroker/分销商动作的后果在本系统之外(公开拍卖行、真实采购)且撤不回,而这批是 POS 页面上同角色员工的日常动作,POS 逐次校验角色、远程连接器逐次写 Audit Log,且大多可撤(`cash_drawer_undo`、`storage_undo_move`、`inventory_count_undo`)。**真正不可撤的两处**:`cash_drawer_close_day` 关掉的 POS 班次(撤销 close 也不重开)、`inventory_count_finalize` 已过账的 Stock Reconciliation(只能 Desk 里取消)——docstring 里写明,别当普通写。若日后要给其中一部分加闸,按本节上面的写法加:`_count()` / `_live()` + `test_doc_counts` 同步。cpa 面只多 5 个读(`cash_drawer_closes` / `cash_drawer_entries` / `cash_drawer_weekly` / `inventory_counts` / `inventory_count_variance`)和 2 个方法 allowlist 名(盘点两个读;抽屉的读走 REST list 与 `query_report.run`,本来就在 cpa 里)。费用 `receipt` 必须是**同一个 POS 用户一天内上传、未挂任何文档、没被别的费用用过**的私有文件 URL(服务端 `_receipt_file` 校验),所以远程面(无 `upload_attachment`)也照常注册 expense:用户在 POS 里自己传图再把 file_url 给工具。

## 命令
- 测试:`uv run --with pytest pytest`(pytest 不在项目依赖里,用 --with 注入)
- 本地运行:`uv run gunstore-mcp`(需环境变量指向目标 Frappe 站点)

## 关键 gotcha
- **版本单一来源** `gunstore_mcp/__init__.py` 的 `__version__`(pyproject 从它取，服务器 serverInfo 也报它)。不再打插件包(2026-09-30 用户决定):远程用户加连接器网址(POS → MCP Settings 有现成命令),本机离线用 `uv tool install --editable` 的 stdio 版。
- **运行实例可能指向 prod POS**。改本仓代码 ≠ 可以拿连接中的 MCP 工具打 prod;联调一律配置指向 `dev.localhost:8000`(gunstore-pos `make dev-up` 起的本地站)。
- 工具行为改动要同步更新 `TOOLS.md`。**工具总数钉在 6 处(外加 `shopfloor` 桶,由 test_doc_counts 的 `_live()` 单独算),改一个就要改全部**:`TOOLS.md` §10 开头 + 文末脚注 / 本文件 / `README.md` / `tests/test_modes.py::test_full_mode_registers_the_whole_surface_including_the_cpa_surface`(总数)/ `tests/test_curated.py::test_curated_tool_count_pinned`(curated 桶)/ `tests/test_distributor.py::test_distributor_tool_count_pinned`(分销商桶)。三条测试是硬闸——加一个模块必然先被它们拦下,这是好事。**注意这个数字本身历史上被低估过两次**(先写 3 处、后写 5 处),每次都是"又冒出一处没跟着改";加新桶时请连同本行一起更新计数。**`tests/test_doc_counts.py` 才是真闸**(它自己算 live 计数再逐处比对,上面这份手写清单按其 docstring 的说法必然会烂);新增**注册期开关**时要同时扩 `_count()` / `_live()`,否则默认面的数字会被开发者自己 shell 里的环境变量决定。
- 服务端签名以 gunstore-pos 源码为准,落码前逐个核对——MCP 只是薄包装,签名漂移不会在本仓测试里暴露(测试全 mock)。

## 部署政策
交付止于 commit/PR + 本地(dev 站)验证。任何对 prod 的调用/发布由主会话经用户确认执行。
