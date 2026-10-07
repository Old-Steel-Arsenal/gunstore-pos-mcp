# GunStore-POS MCP 工具速查（中文）

> 这份文档按「你想干什么」组织，方便直接对 AI 助手说人话。每个工具标注了
> **读 / 写**、是否需要 `confirm=true`，以及关键参数。英文简表见 README.md。

## ⚠️ 先读我：三件事

1. **这个 MCP 连的是生产环境**（pos.oldsteelarsenal.com + 线上商店）。所有"写"
   都是真实业务操作：上架的枪顾客立刻能买、dispose 会登真实枪支账册。**开发/测试
   一律不用它**，用本地 dev 环境。
2. **confirm 机制**：有后果的操作第一次调用会被拒绝，AI 需要带 `confirm=true` 重调。
   这是给你一个反悔的机会——AI 复述要做的事之后你确认了才会真执行。
3. **凭据永远过不了 MCP**：所有密码/API key 字段写入时自动剥除，读也读不到。
   改密钥去 Desk 后台（My Settings / 各 Settings 页）。

`site` 参数：凡是 Woo 相关工具都可选 `site="retail"`（主店 oldsteelarsenal.com，
默认）或 `site="dealer"`（经销商门户）。

---

## 1. 查东西（全部只读，随便用）

| 你想… | 工具 | 说明 |
|---|---|---|
| 按名字/条码/SKU 找商品 | `find_item` | 输入关键词，返回匹配的 Item |
| 查某商品还剩几个 | `item_stock` | 可一次查多个 item_code |
| 查某型号在库的每把枪和各自售价 | `available_serials` | 返回 {型号: [{serial, sell_price…}]}，便宜的在前。**默认剔除寄售在外/暂扣的枪**（和 POS 拣枪口径一致）；盘点要完整清单传 `exclude_unavailable=false` |
| 看全部在库枪支清单 | `firearms_in_stock` | 报表：序列号/厂商/型号/口径/仓库/来源 + FastBound 链接；可按仓库或厂商过滤 |
| 看待处理的柜台/经销商订单 | `pending_orders` | Pending Order 队列：还没 dispose、没收够钱、或还没推 ShipStation 的单子。**寄售出库不在这里**——寄售有独立队列，见第 5 节 |
| 看待发货的网店订单 | `pending_web_orders` | 已付款、等 dispose 的 Woo 订单 |
| 看寄售在途/结算队列 | `consignment_queue` / `consignment_dealer_orders` | 寄售全套见第 5 节 |
| 财务/税务报表(销售、总账、三表、税负、AR/AP) | `sales_report` / `gl_entries` / `financial_statement` / `tax_liability` / `ar_ap_summary` | CPA 报表工具包,全模式可用,见第 10 节 |
| 搜 RSR 批发目录（不是本店库存） | `rsr_catalog_search` | 按关键词/UPC/RSR 编号/厂商编号搜 |
| 跑任意报表 | `frappe_run_report` | 报表名：`Sales Report`（营收+毛利；filters 传 `view`="Order"/"Order Detail"/"Product" 切三种视图，默认 Order，返回含 report_summary 卡片）、`Pending 4473 Orders`（卡在 4473 的单）、`Open Special Orders`（特殊订货看板）、`Pending Transfer Pickups`（待取的转入枪） |
| 查任何记录 | `frappe_list_documents` / `frappe_get_document` | 万能查询，见第 9 节 |

## 2. 商品上架 / 下架（WooCommerce，主店 + 经销商门户）

| 你想… | 工具 | confirm | 说明 |
|---|---|---|---|
| 上架/更新**一把枪** | `woo_push_serial` | ✅ | 按序列号推，SKU = `型号::序列号`。**只动这一把**——日常首选 |
| 下架**一把枪** | `woo_delist_serial` | ✅ | 商品转草稿 + 库存清零，立刻从店里消失 |
| 上架/更新一个**型号的全部在库枪**（或普通商品） | `woo_push_item` | ✅ | 注意：会把该型号下**每一把** Active 的枪都推一遍 |
| 下架整个型号 | `woo_delist_item` | ✅ | |
| 首次整体上架（型号 + 全部序列号一次推齐） | `woo_reconcile` | ✅ | |
| 给一把枪起独立的商品标题 | `set_serial_title` | — | 写 `Serial No.item_name`；**要再 push 一次才生效** |
| 测试商店连接 | `woo_test_connection` | — | 只读探活 |

以上全部支持 `site="dealer"` 推到经销商门户。

**传照片**：单张可用 `upload_attachment`（见第 8 节）；**批量传图+描述+建相册请走
firearm-listing-import 技能的脚本**——它会先把图缩到 2000px（原图太大会把 Woo
推送搞超时），MCP 不做 resize。

## 2b. 上架 / 下架（GunBroker —— 第三销售渠道）

固定价 Buy Now，价取 `Serial No.sell_price`。**只按枪操作，没有"整型号推"这回事**——
GunBroker 上一条 listing 就是一把枪。

**默认只有查的那两个在**。GunBroker 只读工具 2 个（`gb_test_connection` / `gb_listing_status`）
永远注册；GunBroker 写工具 3 个（`gb_push_serial` / `gb_end_listing` / `gb_pull_orders`）
**默认物理不存在**，要用得先 `GUNSTORE_MCP_GUNBROKER_ACTIONS=1` 启动（和分销商队列动作同一套姿态）。
理由不是形式主义：`confirm=` 挡得住手滑，挡不住一个"自己想明白了所以该确认"的 agent，
而用户级实例真的指向 prod。**列表里不存在的工具没法被说服。**

> **部署前置(lead 裁决,已进 G5 部署清单)**：任何要用 GunBroker 上架/结束的 POS MCP 实例
> **必须**设 `GUNSTORE_MCP_GUNBROKER_ACTIONS=1`。**推、结束、拉订单是同一个开关**——
> 别只想着开上架:**能上架却不能结束的实例,正好卡在最危险的位置上**(枪在柜台卖了,
> 助手回"我没有这个工具",listing 还挂在 GunBroker 上等着被第二个买家买走)。
> `gb_pull_orders` 搭在同一个闸上:定时轮询本来就在跑,这个工具只是"现在就跑一轮",
> 但**导进来一张订单会建 POS 单据并预留那把枪**,所以它跟着写面走而不是跟着只读面走。

| 你想… | 工具 | confirm | 说明 |
|---|---|---|---|
| 上架**一把枪** | `gb_push_serial` | ✅（且需开闸） | 守卫拒绝会返回 `{"ok": false, "skipped": ..., "message": ...}`——**这是正常回答不是报错**，照 message 处理，重试不会变 |
| 结束**一把枪**的 listing | `gb_end_listing` | ✅（且需开闸） | **看 `confirmed` 不是看 `ok`**：`confirmed=false` 一定带 `pending_manual` + `gb_url`，意思是**这把枪在 GunBroker 上还能被买走**，要人去站点上手动结束 |
| 看一把枪的上架状态 | `gb_listing_status` | — | 只读；`state` 是 POS 视角（7 态），`remote` 是 GunBroker 当下的说法 |
| 测试 GunBroker 连接 | `gb_test_connection` | — | 只读探活；**回包里的 `sandbox` 字段说明刚才打的是哪个环境** |
| **立刻拉一轮 GunBroker 订单** | `gb_pull_orders` | ✅（且需开闸） | 无参数。回的是**回执不是结果**：`{"queued": true}` 只表示任务已入队(long 队列、与定时轮询同 job 去重,所以撞上正在跑的那轮是"加入"不是"再起一轮"),**不带任何"拉了几单"的计数**——别照着它回报"订单已同步",去看 GunBroker Order 列表和 Error Log。想改拉取起点(`orders_since_override`)只能去 Desk：把水位往回拨会重新导入旧单、重新预留枪 |

**沙盒还是生产，这里选不了**：由目标 POS 站点的 `GunBroker Settings.sandbox_mode` 唯一决定，
而这个字段**经 MCP 的任何一条写路都写不进去**——`enabled` / `sandbox_mode` /
`base_url_override` / `dev_key` / `sandbox_dev_key` / `username` / `password` /
`end_strategy` / `check_deposit_account` / `card_checkout_enabled` 这十个键，带上任何一个，
`update_settings` / `frappe_update_document` / `frappe_create_document` 以及
`frappe_run_method` 的字段 setter **整个调用直接拒绝**（不是剥掉那个键继续写——
半个生效比全不生效更坏）。这些只能在 desk UI 里由人改。

「任何一条写路」是有测试兜着的说法,不是口号:`tests/test_never_writes_surface.py`
遍历已注册工具逐个喂禁写键,并且**静态断言任何写文档体的新工具都必须过这道守卫**。
上一版这句话在只堵住一条路的时候就已经这么写了——**那比不写更糟,因为人会照着它行动**。

工具本身也一律经 POS 的 whitelisted 方法走，MCP 不直连 GunBroker。

**「本地站」不等于「沙盒」**。把 MCP 指向 `dev.localhost:8000`，你拿到的是**那个站点**的
`sandbox_mode`——一个填了生产凭据的本地 dev 站照样能挂出真 listing。唯一可靠的确认方式是
`gb_test_connection` 回包里的 `sandbox` 字段，`gb_push_serial` 之前先看一眼。

**角色不同**：`gb_test_connection` 与 `gb_pull_orders` 要 POS 上的 `SYSTEM_ROLES`，
另外三个（`gb_push_serial` / `gb_end_listing` / `gb_listing_status`）只要 `STOCK_ROLES`。
只有库存类角色的 API 用户会**单单在这两个工具上吃 403**——而探活恰好是文档教你第一个调的，
看到 403 先想这件事，别以为是连接坏了。

**手动重挂**（人工结束过的枪要再上架）不在 MCP 面上：走 Serial No 表单，那里能看见
当初为什么被结束。

## 3. 收货入库 / 库存调整

| 你想… | 工具 | confirm | 说明 |
|---|---|---|---|
| 正式收货（含枪支） | `receive_goods` | ✅ | 建并提交 Purchase Receipt；枪支自动逐把建 FFL Acquisition 并推 FastBound。枪**必须**走这个，不能用 add_stock。**向个人卖家付钱买的货**（`acquisition_source="Individual"`，`acquisition_type` 为空 / `Purchase` / `Individual`，有成本；gunstore-pos #705 起）payload 必须带 `seller_payment_method`（`Cash` / `Zelle` / `Check` / `ACH`），非 Cash 还要 `seller_payment_reference`（支票号 / Zelle 确认号 / ACH 参考号）——POS 随收货同一事务记这笔付款（Cash 出抽屉，其余出银行），返回 `seller_payment`；缺了就整单拒收。Consignment / Gunsmithing / 转移 / 经销商进货不付款、不用带 |
| 给普通商品加库存 | `add_stock` | ✅ | 弹药/配件等非序列号商品 |
| 盘点后把数量改成实数 | `set_stock` | ✅ | 会留盘点原因的审计记录 |
| 标记/取消"待枪匠维修" | `toggle_service_need` | ✅ | 同步开/关枪匠 ToDo |
| RSR 目录商品转成本店在售 Item | `promote_to_item` | ✅ | 厂商/型号/口径/图自动带入 |
| 用 RSR 数据补全已有 Item 的空字段 | `backfill_from_rsr` | ✅ | 只填空，不覆盖已有值 |

## 3b. 盘点 / 现金抽屉 / 库位（POS 1.5.0-beta.15，`tools/shopfloor.py`）

**所有写都要 `confirm=true`**（owner 拍板：全部开放，含钱和库存），但**没有**注册期开关——和 §2b 的 GunBroker 闸有意不同：这些是 POS 页面上同角色员工的日常动作，POS 逐次校验角色，远程连接器逐次写 Audit Log，且大多可撤。**真正撤不回的两处**：`cash_drawer_close_day`（关掉的 POS 班次不会因撤销而重开）和 `inventory_count_finalize`（已过账的 Stock Reconciliation 只能去 Desk 取消）。

### 盘点（Inventory Count）
盘点**只报告枪、从不调整枪**：无 disposition、不调 FastBound；丢枪要人去查（可能要报 ATF）。只有非序列号商品会被 finalize 调整。

| 你想… | 工具 | confirm | 说明 |
|---|---|---|---|
| 看有哪些盘点 | `inventory_counts` | — | 最近 50 个，含状态/范围/扫描数；cpa 可用 |
| 看一个盘点的进度 | `inventory_count_state` | — | 已扫的枪/数量/未知条码 + 应有清单；`counts_only=true` 只要扫描 |
| 看差异 | `inventory_count_variance` | — | `serial.{missing,unexpected,unknown}` 枪只报告；`items[]` 是 finalize 会调的；`open_register_sales` 非空的行要等收银班次关了才能调；cpa 可用 |
| 开始盘点 | `inventory_count_create` | ✅ | 不传范围=全店；仓库必须是本公司自有库存仓 |
| 扫一下 | `inventory_count_scan` | ✅ | 序列号（枪，一把一次）或 UPC（+1，需 warehouse）；`result=error` 表示什么都没存（是回复不是异常）；回包 `entry` 给 undo 用 |
| 手输数量 | `inventory_count_set_qty` | ✅ | 无条码商品；按差值存，不覆盖别的设备的扫描；首次输 0 也算盘过 |
| 手勾/取消一把枪 | `inventory_count_toggle_serial` | ✅ | 标签扫不出时；取消别人的扫描要 Stock Manager |
| 撤一条扫描 | `inventory_count_undo` | ✅ | 只有本人或 Stock Manager |
| 作废一个盘点 | `inventory_count_cancel` | ✅ | 不调整任何东西，扫描留档；Stock Manager |
| **完成盘点** | `inventory_count_finalize` | ✅ | ⚠ 过账**一张** Stock Reconciliation 把非序列号商品调成实数。`item_rows=[{item_code,warehouse}]` 选行；不传=所有有差异的行**除了**没人扫过的（那些只有在列出时才会被写成 0）。先读 variance、和用户确认再调。无扫描/期间已冻结/有未关收银班次都会被拒（不留痕）|

### 现金抽屉（Cash Drawer）
规则见 POS 仓 `docs/cash-drawer.md`。抽屉应有的现金 = Cash 科目余额 + 未合并 POS 发票的现金。

| 你想… | 工具 | confirm | 说明 |
|---|---|---|---|
| 看今天的抽屉 | `cash_drawer_today` | — | expected、今日/上次关账以来的流水（含 `can_undo`）、阈值、费用上限、可选费用科目 |
| 预览关账 | `cash_drawer_preview_close` | — | 只算不记：expected / variance / needs_reason / first_count |
| 看每日关账记录 | `cash_drawer_closes` | — | 默认不含已撤销的；cpa 可用 |
| 看抽屉流水（存款/从银行取现/费用/卖家付款） | `cash_drawer_entries` | — | 默认只看 Posted；cpa 可用 |
| 现金流水 Log | `cash_drawer_log(from_date,to_date)` | — | 逐笔现金进出（收银机按每张小票，合并后也拆开）、经手人、单据、逐笔余额、期初/期末、进出合计；默认近 7 天、≤1 年；cpa 可用 |
| 给会计的周报 | `cash_drawer_weekly(from_date,to_date)` | — | Cash Drawer Weekly 原样透传，末尾有对账校验块（差额必须 0.00、未分类行应为空）；≤400 天；cpa 可用 |
| **关账** | `cash_drawer_close_day` | ✅ | ⚠ 清点抽屉、关 POS 班次、记差额分录；**公司首次盘点**改为把账调到实际现金（CASH-CUTOFF）。差额≥设置阈值（默认 $20）必须写 reason。先 preview，把 `expected` 作为 `expected_seen` 传入（账动了会被拒） |
| 存款/从银行取现/费用 | `cash_drawer_record_entry(kind=deposit\|from_bank\|expense)` | ✅ | 从银行取现任何柜台角色可记、`reference` 可选；费用须 `expense_account`+`memo`，`receipt` 可选（POS 1.8.2 起；更早的 POS 仍要求收据），有上限（默认 $200），只能走允许科目；**不属于该 kind 的参数会被拒绝而不是悄悄丢掉** |
| 撤销 | `cash_drawer_undo(entry\|close)` | ✅ | 恰给一个：撤某条流水（之后有清点则拒；撤卖家付款要 System Manager）/ 撤**最新**一次清点（班次不重开）。manager |
| 改旧式卖家付款的方式 | `cash_drawer_record_payout(acquisition, method)` | ✅ | 只改 1.8.4 前旧式卖家付款的方式或重记撤销的那笔；**不能新建**（1.8.4 起卖家随收货单付款）。manager |

**费用收据规则**（服务端 `_receipt_file`，仅在传了收据时校验）：`receipt` 必须是**同一个 POS 用户 1 天内上传**、仍私有、**未挂在任何文档上**、且没被别的费用用过的文件 URL。**远程连接器没有 `upload_attachment`**，所以用户要在 POS 里自己传图（私有 File，不挂文档），再把 file_url 交给 `cash_drawer_record_entry`；本机 stdio 版可用 `upload_attachment(file_path, is_private=true)`（不要传 doctype/name）。因为服务端本来就接受这种收据，远程面**照常注册** expense，不需要摘掉。收据不合规时由 POS 拒绝，什么都不会入账。

### 库位（Storage Locations）
**只动追踪层**：不建任何库存/会计单据，不改库存和账。Slots 区=编号槽位（A1,A2…，每槽一把枪）；Open 区=**一个**同名位置（货架/展柜/保险柜，不限量，枪和别的都能放）。

| 你想… | 工具 | confirm | 说明 |
|---|---|---|---|
| 看全店库位图 | `storage_map` | — | 每区每位的内容 + `unassigned` + `plans`（每个房间的平面图：区/墙/门/区块/标签的位置尺寸，单位英尺；只读，在 POS 页面里画）；`zones_only=true` 只列区（便宜） |
| 看某个位置里有什么 | `storage_location` | — | 位置名即条码 |
| 某把枪/某商品在哪 | `storage_where` | — | `serial_no` → 它的 `storage_location`；`item_codes` → 各位置数量 + 未入位数量 + `sole` |
| 还没入位的 / 待确认的 | `storage_unassigned` | — | `to_confirm` = 卖出/发货时没说从哪个位置拿的商品 |
| 建区 | `storage_create_zone` | ✅ | `kind`=Slots（`count` 个槽）或 Open（一个位置，`count` 忽略）。Slots 可选 `sides`（几面，双面架=2，0/不填=按图上形状；Open 区给了就拒）、`numbering`（`Odd / even` 默认：一面 1,3,5… 对面 2,4,6…；`In order`；只对两面生效）；需 POS ≥ 1.8.5（更老的 POS 会静默忽略这两项）。Stock Manager |
| 给 Slots 区加槽 | `storage_add_positions` | ✅ | 不重排不删除；Open 区拒绝 |
| 停用/启用 区或位置 | `storage_set_disabled` | ✅ | 恰给 `zone` 或 `location` 之一；停用要求为空 |
| 把枪/商品放进位置 | `storage_scan_move` | ✅ | **这一个工具就是「指派序列号」和「放入数量」**（`code`=序列号 / UPC；`qty`）。来源不唯一时什么都不动、`result=choose` 列 `options`，带 `from_location` 重调。`error` 是回复不是异常 |
| 撤销一次移动 | `storage_undo_move` | ✅ | 仅手动移动、仅一次、且东西还在原处；`ok=false` 是回复 |
| 确认待确认商品从哪拿的 | `storage_confirm_taken` | ✅ | 这是**唯一**的「从位置里拿走数量」动作；不超过待确认量 |

## 4. 订单 → 收款 → 发货（Pending Order 队列的全部动作）

| 你想… | 工具 | confirm | 说明 |
|---|---|---|---|
| 给没付清的单子记一笔收款 | `record_payment` | ✅ | 不填金额=收清尾款；Zelle/ACH 必须带 transaction_number;Payroc 开着时 Credit Card(虚拟终端收的)也必须带授权码 |
| **Dispose** 柜台/经销商订单的枪 | `dispose_order` | ✅ | 逐把登转出 disposition：出库存 + 推 FastBound。没付清或收货方 FFL 无效会被服务器拦下。**动手前先核对 FFL 和序列号** |
| **Dispose** 网店订单的枪 | `dispose_web_order` | ✅ | 网店单不推 ShipStation（店里的 Woo 插件自己发货） |
| 把订单推到 ShipStation 买面单 | `push_shipment` | ✅ | 幂等；FFL 无效/没付清会失败保护 |
| 不走 ShipStation、直接标记已发货 | `mark_shipped_manually` | ✅ | 兜底：面单在别处买的/集成关了。枪没 dispose 完会拒绝 |
| **取消一张柜台/经销商转移单** | `cancel_order` | ✅（必须带 reason） | 安全级联：撤 disposition→库存回冲→FastBound 删除排队→ShipStation 作废 + 按实收退款（refund_mode/refund_reference）。别手工逐张撤——这个通道就是为此建的 |
| 测试 ShipStation 连接 | `shipstation_test_connection` | — | 只读探活 |

典型流程：`pending_orders` 看队列 → 差钱先 `record_payment` → `dispose_order` →
`push_shipment`（网店单则 `pending_web_orders` → `dispose_web_order`，不用推单）。

## 5. 寄售出库（Consignment Out —— At Dealer 队列全生命周期）

寄售有自己的队列和流程，**不走第 4 节的 Pending Order**。

| 你想… | 工具 | confirm | 说明 |
|---|---|---|---|
| 看在途寄售队列 | `consignment_queue` | — | 每张寄售单一张卡：经销商可寄性、ShipStation 状态、tracking、枪 pills；`include_closed=true` 连历史一起看 |
| 看能寄给哪些经销商 | `consignment_dealers` | — | 全部 FFL dealer 客户 + shippable/block_reason（FFL 过期会标出来） |
| 看哪些枪能寄出 | `consignment_serials` | — | 在库 Active 序列号 + 结算价/参考价；不可选的枪也返回并附原因 |
| 建一张寄售单 | `create_consignment_out` | ✅ | payload：{dealer, lines:[{item_code, serial, cost, msrp}], dispose_now?}。默认存草稿稍后 dispose；`dispose_now=1` 立即 dispose+推 ShipStation（被 FFL 门拦下会降级保留草稿） |
| **Dispose（发出）寄售单** | `ship_consignment_out` | ✅ | 逐枪登 FFL 转移 disposition + 移库 + 推 FastBound；先验全部行再动任何行，幂等。**动手前核对经销商 FFL 和序列号** |
| 推到 ShipStation 买面单 | `push_consignment_shipment` | ✅ | 已 dispose 的单才能推；幂等、失败不留脏数据 |
| 不走 ShipStation、手工记发货 | `mark_consignment_shipped` | ✅ | 可带 tracking_number/carrier，经销商门户会显示 |
| **改在外寄售枪的价**（At Dealer 行的 Dealer Price/MSRP） | `update_consignment_prices` | ✅ | prices：{行名: {cost 必填>0, msrp 三态——缺键=不动、空=清掉、否则>0}}。只改本张单的行快照，不动 Serial No/Item 主档；结算与门户自动跟随。仅 At Dealer 且未出结算发票的行；整批先验后写。行名从 `consignment_queue` 拿 |
| 看结算队列（卖掉但没收到钱的） | `consignment_dealer_orders` | — | Sold 但结算发票没出/没付清的行，失败的排最前 |
| 结算发票失败重试 | `retry_consignment_invoice` | ✅ | 传 Consignment Out Line 名（从结算队列拿） |
| 收回没卖掉的枪 | `return_consignment_lines` | ✅ | 逐枪登真实 re-acquisition（自动推 FastBound）+ 移库回主仓 |
| 撤销寄售（整单草稿 / 单行误发） | `cancel_consignment` | ✅（必须带 reason） | 不带 `line` 撤整张草稿；带 `line` 撤一行已发的（"枪其实没离店"，仅结算前可用） |

**结算是自动的**：经销商在门户点 Mark Sold 后系统自动出结算发票（失败进结算队列重试）。
**撤结算**：用 `frappe_cancel_document` 取消那张结算 Sales Invoice——取消钩子会对称反开父单（Closed→Shipped），无需也没有专用端点。
**代经销商签收/报售（mark_received / mark_sold）做不了**：那两个方法绑定门户 dealer 会话身份，管理密钥调用会被拒；见第 11 节。

## 6. 4473 / 合规（FastBound、ATF）

| 你想… | 工具 | confirm | 说明 |
|---|---|---|---|
| 给柜台枪支销售发起 4473 | `start_4473` | ✅ | 发票挂起，去 FastBound 填表；参数是 {发票行: 序列号} 映射 |
| **解卡**：4473 在 FastBound 明明完成了但单子卡住 | `manager_override_4473` | ✅ | 经理权限 + 必须写原因；补出 Retail Sale disposition（标 manual_override 可审计）。**不回推 FastBound**，账册要另行核对 |
| 发起客户转入枪的 4473（收转移费） | `start_transfer_4473` | ✅ | 服务器端建 $0 枪行 + 转移费行的 POS 发票；费率用 `frappe_run_method` 调 `ffl_core.firearm.get_transfer_config` 查 |
| 核验一个 FFL 号（eZ-Check） | `atf_verify_ffl` | ✅ | 在线验证并存/更新 ATF FFL Record |
| 核验某供应商的 FFL | `verify_supplier_ffl` | ✅ | 顺带更新供应商上的核验状态 |
| 把所有 FFL 供应商重验一遍 | `reverify_all_ffls` | ✅ | 批量 |
| 修正已入册枪支的厂商/进口商 | `push_serial_to_fastbound` | ✅ | 原地改 FastBound 账册条目 |
| 对账：FastBound 已 dispose 但本店还显示在库 | `boundbook_reconcile` | 干跑不用；`apply=true` 才要 ✅ | 默认只报告不动库存 |
| 测试 FastBound 连接 | `fastbound_test_connection` | — | 只读 |

## 7. 分销商目录

目录/库存 feed 由数据服务(Data Service Settings 所指,线上=osa-api)服务端同步(RSR 与 Sports South 都是),POS 侧没有手动同步开关。探活用 `distributor_test_connection`;目录健康看 Desk 的 Catalog Service 页。

## 8. 设置 & 文件

| 你想… | 工具 | 说明 |
|---|---|---|
| 看某个集成的配置 | `get_settings` | `ffl` \| `fastbound` \| `rsr` \| `payroc` \| `woocommerce` \| `dealer` \| `shipstation` \| `gunbroker` \| `sports_south` \| `data_service` |
| 改配置（非密钥字段） | `update_settings` | 密码/密钥字段自动剥除，去 Desk 改 |
| 上传一个本地文件到 POS | `upload_attachment` | 可顺带挂到某条记录（doctype+name）或写进附件字段。默认私有；**要给 Woo 用的商品图必须 `is_private=false`**。批量图片走技能脚本（先 resize） |

## 9. 万能后门（`frappe_*` 通用工具）

上面没有的操作，AI 可以用通用工具直达任何数据和白名单方法——**新功能上线当天就能用，
不用等 MCP 更新**：

- `frappe_list_documents` / `frappe_get_document` / `frappe_describe_doctype` — 查任何 doctype（先 describe 看字段名）
- `frappe_create_document` / `frappe_update_document` — 建/改任何记录（凭据字段自动剥除）
- `frappe_delete_document` / `frappe_submit_document` / `frappe_cancel_document` — 删/提交/作废（都要 confirm）
- `frappe_run_method` — 按点路径调任何白名单方法；方法名含 delete/cancel/refund/**dispose/push/charge/consolidate/ship/return/receive/sold/settle/onboard** 等危险动词时要 confirm。另有一批**无危险动词但高后果**的方法走显式精确名单(`_ALWAYS_CONFIRM_METHODS`:update_order / update_consignment_line_prices / create_consignment_out / record_payment / create_consignment_invoice_now / add_stock / set_stock / set_customer_tax_exempt / 盘点·现金抽屉·库位的全部写方法 / trade_in.create_trade_in_intake / cost_correction.correct_serial_cost),裸调同样要 confirm——收录判据:记钱、动库存、改合规/税务状态
- `frappe_run_report` — 跑任何报表

**尚无专用工具、常用点路径备忘**（都走 `frappe_run_method`）：

| 场景 | 点路径 |
|---|---|
| 手动合并卡住的 POS 发票（枪不出库存时的解药） | `ffl_core.api.pos_consolidate.consolidate_pos_invoice_now`（要 confirm） |
| 核验**客户**的 FFL | `ffl_integrations.atf.ez_check_api.verify_customer_ffl` |
| 单枪与 FastBound 的字段差异对账 | `ffl_integrations.fastbound.reconcile.compute_serial_fb_diff`（只读）等 reconcile 套件 |
| 特殊订货 / 定金 | `ffl_core.api.special_order.*` |
| 个人 trade-in 收枪（payload 的 `payout_method` = Cash/Zelle/Check/ACH，不抵扣信用时必填，成功后记 CASH-PAYOUT 现金分录；`apply_credit` 则走信用不付现；**要 confirm**） | `ffl_core.api.trade_in.create_trade_in_intake` |
| 修已入册枪的成本（`payout_was_different=1` 才会按差额记付款分录；**要 confirm**） | `ffl_core.api.cost_correction.correct_serial_cost`（先 `list_item_serials_for_cost` 查） |
| 安全删除 Item（保留枪支审计链） | `ffl_core.api.item_admin.preview_delete` → `force_delete`（要 confirm） |
| **编辑**一张 pending 柜台单（取消重建式，仅限未 dispose/未推单） | `ffl_core.api.manual_order.update_order`（要 confirm——已列入显式高后果名单 `_ALWAYS_CONFIRM_METHODS`，"update" 虽不在危险动词表，裸调也会被要求确认）内部是 cancel+rebuild 级联——慎用，动手前先复述要改什么 |
| 查/设客户免税状态 | `ffl_core.api.manual_order.get_customer_tax_status` / `set_customer_tax_exempt` |
| Woo 部分退款对账（Woo 退了款、POS 侧对齐） | `ffl_woo_sync.woocommerce.refunds.reconcile_web_order_refund`（要 confirm） |
| 清理指向已删 Woo 商品的 dangling ID | `ffl_woo_sync.woocommerce.dangling.woo_audit_dangling_ids`（`fix=0` 干跑只报告） |
| 经销商开户（FFL 查询 → 建 Customer+门户账号） | `osa_consignment.api.dealer_onboarding.lookup_ffl` → `onboard_dealer`（要 confirm） |
| 网单收入发票失败重试 | `ffl_woo_sync.woocommerce.revenue.create_web_invoice_now` |
| 撤销一笔寄售结算 | `frappe_cancel_document` 取消那张结算 Sales Invoice（钩子自动反开父单） |

## 9b. 分销商直发（RSR Direct Connect / Sports South）— `distributor_*` 12 个（只读 8 + 动作 4）

只读 8 + 确认队列动作 4。**不含**直接下单(place)、Settings 写、以及 metabox 清单以外的任何变更面。

| 工具 | 服务端方法 | 说明 |
|---|---|---|
| `distributor_orders` | `distributor.api.list_orders` | 列 Distributor Order,可按 status/分销商筛 |
| `distributor_route_queue` | `distributor.router.route_queue` | **确认队列**:待确认的 Draft 单 + 被拦下的网单(未付款/买家 FFL 缺失或过期/地址不全)及原因、目的 FFL 到期日。确认任何单之前先读这个 |
| `distributor_catalog_search` | `distributor.api.search` | 目录 typeahead(本地同步的目录,不是实时库存);不指定分销商时跨所有 enabled 家 |
| `distributor_test_connection` | `distributor.hub.test_connection` | 探活一家分销商:数据服务目录健康 + 下单 API 凭据(RSR Direct Connect 两账户 / Sports South orders+invoices)。只读;`ok: null` = 未配置/不适用,不是失败 |
| `distributor_quote` | `distributor.api.quote` | 单品成本/MAP/MSRP/建议价/受限州/封锁旗标;qty 是**缓存目录量**,不保证新鲜度 |
| `distributor_check_availability` | `distributor.api.check_availability` | **实时**量价二次确认(会打 RSR HTTP,只读) |
| `distributor_precheck_fds` | `distributor.router.precheck_fds` | 问分销商是否接受发往该 transfer dealer 的 FDS(会打 HTTP,只读) |
| `distributor_fulfillment_options` | `distributor.options.fulfillment_options_for_order` | 单张网单的**决策面板**:逐行候选分销商(最便宜在前)+ 本地库存对比行 + 裁决。只读。**读返回值前先看下面的三态说明** |
| `distributor_confirm_order` | `distributor.api.confirm_order` | ⚠ **要 confirm**。Draft→Queued 并启动下单 worker = **真实采购不可逆**;RSR 无取消 API。柜台来源单还要求发票全款结清 |
| `distributor_cancel_order` | `distributor.api.cancel_order` | 要 confirm + **要 reason**。仅在下单前(Draft/Queued)是安全的;已下单的会被标记并告警运营,退货是人工流程 |
| `distributor_reroute` | `distributor.router.reroute` | 要 confirm。修好拦截原因后重跑路由,**只建 Draft**、幂等 |
| `distributor_update_order_ffl` | `distributor.router.update_order_ffl` | 要 confirm。FDS Hold 的标准解法;新执照先过与柜台同一条 canonical 校验链(未过期+完整地址)才调 RSR |

### `distributor_fulfillment_options` 的返回值契约(容易读错,单列)

**两个字段是三态,`if not x` 在它们身上是错的**——`null` 的意思是"还判不了",不是"没问题":

| 字段 | 取值 | `null` 的含义 |
|---|---|---|
| `verdict.fulfillable` | `true` / `false` / `null` | 判不了,看同级 `reason`:`unknown_destination`(目的州还没捕获——订单停在 Pending Route 或买家 FFL 未上传时**这是常态**)或 `stale_feed`(唯一够量的候选来自过期 feed) |
| `restricted_state` | `true` / `false` / `null` | 同上,受限州判定尚无法做出 |

遇到 `null` 一律当 **HOLD**:如实说"未判定"并引用 `reason`,**不要说这行可以发货**。受限商品 + 未解析目的地正是这里绝不能放行的情形——POS 侧刚修掉的就是这个 fail-open,消费端读成 falsy 等于在自己这边重新打开它。

**候选行旗标**:`not_carried` = 该分销商目录里没有这个商品(**列出来**而不是丢掉,以免被误读成缺货);`blocked` = 有货但不可买(分销商封锁 / 需厂商批准);`stale` = 数量来自过期 feed。不可买的行一律**沉到最后**,所以第一条候选是**最可能可执行**的那条——但以该行的 `verdict` 与旗标为准,排序本身不是许可。

**金额**:`unit_landed` 是**单件**、且**只含货款**。运费在下单前不存在,故 `shipping` 为 `null` 且 `shipping_known` 为 `false`。不要把 `unit_landed` 说成到岸价,也不要乘以数量当成最终成本。

## 10. CPA 模式（只读会计面）+ 报表工具包

**模式开关**：启动环境变量 `GUNSTORE_MCP_MODE=cpa`（默认 `full` = 全部 115 工具中默认注册 108（4 个分销商队列动作 + 3 个 GunBroker 写动作需显式开启），行为与以前完全一致；未知值直接拒绝启动，不会静默降级成可写）。cpa 模式给会计/CPA 用：**写面在工具列表里物理不存在**，不是"存在但会拒绝"。三层防御，缺一层其余仍兜底：

1. **注册层**：tools/list 恰好 = 下面 26 个名字（集合相等，测试钉死）；
2. **客户端层**：一切写方法 + 未逐一列名的点路径方法（`frappe_run_method` 整个不注册）→ `CpaModeRefused`；只读点路径 allowlist 逐一列名，禁通配；
3. **Settings 层**：7 个集成 Settings doctype 的 get/list 读也被挡（配置面对会计无用，密码遮蔽是框架行为不是本仓保证）。

**cpa 模式的 26 个工具**：
- 通用查（4）：`frappe_list_documents` / `frappe_get_document` / `frappe_describe_doctype` / `frappe_run_report`
- 业务只读（9）：`find_item` / `item_stock` / `firearms_in_stock` / `pending_orders` / `pending_web_orders` / `consignment_queue` / `consignment_dealers` / `consignment_serials` / `consignment_dealer_orders`
- 盘点 / 现金抽屉只读（6，见 §3b；**full 模式同样可用**）：`cash_drawer_closes` / `cash_drawer_entries` / `cash_drawer_log` / `cash_drawer_weekly` / `inventory_counts` / `inventory_count_variance`。cpa 没有任何一个写。盘点两个读要求该 API 用户有 Stock 角色（POS 端 `COUNT_ROLES`），抽屉四个读 Accounts User 即可
- 报表工具包（7，见下；**full 模式同样可用**）

**远程连接器(OAuth,免密钥)**:`GUNSTORE_MCP_TRANSPORT=http` 时本服务器是 POS 的 OAuth 资源服务器——用户在 claude.ai / Claude Code 填网址、浏览器登录 POS 点允许即可,**每次调用以登录人本人的 POS 角色执行**,只收 Claude 连接器(动态注册的客户端)签出的令牌,**每次调用都在 POS 的 Connector Audit Log 留永久记录**(谁、哪个连接器、哪个工具、参数(秘密打码)、成败;记不上就不执行);cpa 三层闸照旧;全量面远程是 107 个(`upload_attachment` 读服务器本地路径,远程永不注册);分销商动作远程永不开,GunBroker 三个写动作只在 full 面开——POS 部署按该店 GunBroker Settings 的 enabled 自动设闸。细节见 README「Remote connector」。

注意 cpa 模式**没有** `available_serials`（其默认剔除寄售/暂扣枪，在盘点语境会漏枪——盘点用 `firearms_in_stock`）。

**账本在 QuickBooks(owner 裁定 2026-09-02)**:ERPNext 是业务系统与数据源,不是账本。喂 QB 的是 `sales_report` / `inventory_receipts` / `tax_liability` + 标准报表 Stock Balance;`financial_statement` / `ar_ap_summary` 只作参考(ERPNext 总账不录费用、不录供应商发票,SRBNB 长期挂账)。

**报表工具包**（口径权威 = run 2026-07-16-mcp-cpa-mode/cpa-review.md §2/§3）：

| 你想… | 工具 | 说明 |
|---|---|---|
| 看期间营收+毛利（报税视图） | `sales_report(from_date, to_date, view="Product", channel?, product_type?)` | Sales Report 原样透传（含 report_summary 卡片）；view: Order / Order Detail / Product；channel: POS / Web / Manual |
| 看期间**入库**了什么（枪/弹药/配件，从哪来、多少钱） | `inventory_receipts(from_date, to_date, view="Units", category?, receipt_class?, supplier?, include_transfers=False, include_custody=False)` | Inventory Receipts 报表原样透传（含卡片）；直接读库存流水（actual_qty>0），Cost Received 与 Stock In Hand 对得上；view: Units（默认，逐支一行）/ Summary（品类 × 入库类型汇总）/ Receipts（Desk 树形，行带 indent）；类型 Purchase / Trade-in / Consignment / Intake / Return / Adjustment / Revaluation（成本修正差额，件数 0，按存货调整记）；仓内调拨与顾客托管枪默认不出，开关或点名该类型才出；标记 No cost / No A&D |
| 追总账明细 | `gl_entries(from_date, to_date, account?, party?, voucher_no?, voucher_type?, limit=500)` | 恒定 `is_cancelled=0`（cancel+amend 被撤单自动出列）；**截断显式** `truncated:true`，绝不静默截断；limit 夹 1..5000（0/空按 500），更多行用日期范围分页；单公司口径——多公司化需补 company filter |
| 跑三大财务报表 | `financial_statement(statement, from_date, to_date, periodicity="Monthly")` | statement: `pnl` / `balance_sheet` / `trial_balance`；P&L/BS 走 Date Range;Trial Balance 需日期落在同一 Fiscal Year（自动解析,跨年拒绝） |
| 查期间销售税负债滚动表 | `tax_liability(from_date, to_date)` | opening/collected/remitted/closing 按 voucher 分列,非常规 voucher fail-closed 单列;科目动态解析自默认销售税模板;**注意发票的 "Total Taxes and Charges" 含运费,不是销售税** |
| 查 Payroc 刷卡流水并和 POS 对账（柜台+网单） | `payroc_transactions(from_date, to_date)` | 实时读 Payroc 网关、按日期列出全部交易（type = SALE / REFUND；status 里有 COMPLETE / READY / DECLINED / VOID 等；含 portal 里做的退款与作废），逐行按 orderId 对到 POS（柜台 = POS/Sales Invoice 名，网单 = Woo 订单号）；`flags` 标出不一致:POS 无记录 / 金额不符 / POS 记已收但网关作废 / 退款 POS 未记;`pos_only` = POS 记了但日期搜索没返回的(逐笔回查);`summary` 只算钱实际在手的(COMPLETE/READY);卡号只出类型+后 4 位;每次最多 31 天,`truncated:true`(服务端翻页约 75 秒封顶)就拆段查;API 用户需 System Manager / Accounts Manager / Accounts User 之一;需要带 `payroc/ledger.py` 的 POS 版本;只读 |
| 查应收/应付账龄 | `ar_ap_summary(kind, as_on_date)` | kind: `ar` / `ap`;Posting Date 基准,30/60/90/120 账龄桶;寄售结算应收在 AR 里按经销商列示。**`ap` 不适用**:账本在 QuickBooks、进货预付,ERPNext 不录 Purchase Invoice(直发单的 RSR 应付除外) |

**月结/报税常用标准报表**（`frappe_run_report` 直跑,键名已核对 ERPNext v16 源码）：

| 报表名 | 关键 filter 键 |
|---|---|
| `Sales Register` | company, from_date, to_date, customer, warehouse, mode_of_payment, item_group |
| `General Ledger` | company, from_date, to_date, account, party_type+party, voucher_no, categorize_by |
| `Stock Balance` | company, from_date, to_date, item_code, item_group, warehouse |
| `Accounts Receivable` / `Accounts Payable` | company, report_date, ageing_based_on("Posting Date"/"Due Date"), range("30, 60, 90, 120") |
| `Trial Balance` | company, **fiscal_year(必填)**, from_date, to_date |

（缓建备忘：`stock_valuation` 专用工具——年终存货 tie-out 直接 `frappe_run_report("Stock Balance", …)` 即可。）

## 11. 这个 MCP **做不了**的事（别硬试，走别的路）

| 做不了 | 替代路径 |
|---|---|
| 改密码/API 密钥类字段 | Desk 后台直接改（设计如此，防泄露） |
| 批量传枪支照片并建相册 | firearm-listing-import 技能的脚本（自动 resize + 建 gallery） |
| 改 doctype 结构/权限/角色 | 走代码和迁移，MCP 写入黑名单挡着 |
| Payroc 刷卡/退款 | POS 收银界面操作（真实资金，未包装成工具） |
| 在 FastBound 填 4473 表格本身 | FastBound 网页 UI（表格只能在它家填） |
| 代经销商在门户签收/报售（mark_received / mark_sold） | 那两个方法绑定门户 dealer 会话身份，管理密钥调用会被拒；让经销商自己在门户点，或等 staff 端代操作方法上线 |
| 手动触发 ShipStation tracking 轮询 | 每 10 分钟 cron 自动跑（poll_consignment_tracking 非白名单方法）；急查去 ShipStation 后台 |
| 给 dev/测试环境做操作 | 本地 `bench --site dev.localhost` + 本地 WC 克隆 |

---

*工具总数 115（10 个通用 + 56 个专用 + 12 个分销商 + 7 个报表 + 30 个门店运营），默认注册 108（4 个分销商队列动作需 `GUNSTORE_MCP_DISTRIBUTOR_ACTIONS=1`；3 个 GunBroker 写动作需 `GUNSTORE_MCP_GUNBROKER_ACTIONS=1`）；`GUNSTORE_MCP_MODE=cpa` 只读模式恰注册其中 26 个。对应版本 v0.9.1；工具行为以 README.md
和源码 `gunstore_mcp/tools/` 为准。*
