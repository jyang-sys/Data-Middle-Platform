# Base 数据中台

基于 FastAPI、SQLite 和原生 HTML / JavaScript 的单账号开发环境。从 Base（BaseLinker）自动读取产品、库存、订单和平台店铺，以独立数据表存储，供后续销量、人员业绩和小组业绩报表使用。

## 环境启动

Python 3.12；校验脚本另需 Node.js。锁定依赖见 `requirements.lock`。

```bash
bash scripts/install.sh
bash scripts/check.sh
bash scripts/start-all.sh
```

最后一条命令同时运行本地 Web 服务和自动同步进程；Ctrl+C 停止二者。服务监听 `127.0.0.1:8000`，进入“中台基础数据”查看同步状态、产品父子 SKU、库存明细、店铺及订单归属。`/docs` 提供接口文档。云环境是否提供浏览器预览取决于宿主能力。

也可分别启动：

```bash
bash scripts/start.sh
# 在另一个终端启动，保持运行
bash scripts/worker.sh
# 仅执行一次完整同步周期
bash scripts/worker.sh --once
```

密钥通过运行环境安全注入 `BASELINKER_API_TOKEN`，允许访问 `api.baselinker.com`。`.env.example` 仅列出配置；脚本不自动加载 `.env`。不要把密钥、订单 JSON、数据库或客户资料提交到仓库。

- `DATA_PLATFORM_DB`：默认 `data/platform.sqlite3`，API 和 worker 必须使用同一个路径。
- `BASE_SYNC_INTERVAL_SECONDS`：默认 900，表示每轮完成后等待 900 秒再开始，不代表保证每 15 分钟全部数据更新。
- 自动同步进程仅应运行一份。API 手动同步与 worker 共用数据库任务锁和请求节流。

## 本次实现

| 数据域 | 独立存储 | 行为 |
| --- | --- | --- |
| 平台店铺 | `base_platforms`、`base_shops` | 使用平台编码与店铺 ID 的组合区分店铺，同步名称。 |
| 产品 | `base_catalogs`、`base_products` | 获取全部可访问目录、包含变体的产品列表及详情；保留 `parent_id`、父 SKU、子 SKU 和原始 JSON。 |
| 库存 | `base_warehouses`、`base_stock` | 按目录、产品、仓库保存最新完整快照，保留库存、预占、供应商待入库原字段及抓取时间。 |
| 订单 | `base_orders`、`base_order_items` | 不限最近 N 天，按 ID 分页读取可访问历史，包括未确认订单和各状态订单，保留接口返回的全部字段及明细。 |
| 订单本地归属 | `order_annotations` | 独立维护每笔订单的销售负责人、业务小组及任意 JSON 附加字段；源订单刷新不会覆盖这些内容。 |
| 同步任务 | `base_sync_jobs` | 记录开始、结束、成功或失败状态、已保存数量及订单分页位置。 |

“分开存储”指在同一个数据库中分域建表，不是四套物理数据库。源数据与业务维护数据分离，方便后续数据库迁移和分析建模。

订单全量指 **Base 当前账户可访问的全部订单历史，以及 `getOrders` 返回的全部字段**。请求开启自定义附加字段、佣金、Base Connect 和折扣字段；不会自动调用每种订单关联资源接口，也不会下载附加字段中的文件链接。API 未提供或权限不可见的数据不在该定义内。

每轮订单同步从 ID 0 开始重新扫描，更新旧订单状态和附加字段，按订单 ID 幂等写入；不存在时间窗口或 10,000 条业务上限。某页失败时保留此前成功保存页面，并将任务标为失败；重跑从头核对，不跳过失败范围。上游删除的订单保留本地记录，目前不识别删除事件。

产品和库存只有所有目录抓取完成后才提交新状态。库存快照会替换旧快照，产品来源移除时标记 inactive 并保留历史记录。库存尚不存每日历史快照。同步请求预算按同一数据库共享，约 83 次/分钟；此预算不包括其他系统使用同一 Base 账户发出的请求。

进程意外退出后，任务可能停留在 running。确认旧同步进程和 API 同步任务已停止，再运行：

```bash
bash scripts/worker.sh --once --recover-interrupted
```

## 订单归属维护

页面中填写订单 ID，先读取当前归属，再保存负责人、小组及自定义 JSON；只修改中台，不写回 Base。

接口示例：

```http
PATCH /api/base/orders/123/annotation
Content-Type: application/json

{
  "sales_owner": "张三",
  "business_group": "一组",
  "extra_fields": {"活动": "双十一", "备注": "跨组借货"}
}
```

PATCH 未传的字段保留原值；负责人或小组传 null 表示清空；`extra_fields` 传入时整体替换该 JSON 对象。负责人和小组目前为文本字段，尚未接入员工与小组主数据、归属变更审计或核算规则。

## 接口与报表边界

- `GET /api/base/status`：各数据域数量及最近同步任务。
- `POST /api/base/sync/{domain}`：同步 `shops`、`products`、`inventory`、`orders` 中某一域；同步期间请求会保持连接。
- `GET /api/base/products`、`/inventory`、`/orders`：分页浏览，默认 50 条；支持 `limit`、`offset`。
- `GET /api/base/shops`：店铺列表。
- `GET /api/base/orders/{id}`：源订单完整 JSON 和独立的本地归属。
- `PATCH /api/base/orders/{id}/annotation`：修改本地归属。

现有“销售分析”和 `/api/sales/*` 是原有独立演示/分析模块，仍使用已确认订单、确认时间、币种和部分金额字段；它不读取新 `base_*` 数据表，也不代表需求评审稿的正式销量口径。新中台数据已为后续报表准备，人员、小组业绩报表本次尚未实现。

Base `date_add` 是 Base 订单创建时间，不能未经核对就视为平台原始下单时间。父子 SKU 来源于 Base 商品关系，尚不保证父 SKU 等同产品型号，也不自动解决跨平台同规格编码映射。库存的 `incoming` 表示供应商待入库，不能直接认定为海外运输在途；库存是否为实物或虚拟、可用量如何扣减、退货状态及小组归属仍需核对现有字段。

## 数据保存与部署

开发环境保留 API 返回的完整订单 JSON，包含客户资料；仅绑定回环地址。对外多人使用前须实现登录、角色权限、审计和 HTTPS，并按实际数据量评估 PostgreSQL、任务队列及日志增量同步。当前没有公网部署。

数据库开启 WAL 和外键。备份前停止 API 与 worker，再复制 `data` 目录；备份也不能提交 Git。重启服务不会清空已有数据。

仓库内 GitHub Actions 执行依赖安装和自动测试，不注入真实 Base 密钥。测试使用临时数据库及模拟上游，覆盖已有工具和新数据域，验证父子关系、分页、重复同步、源字段保留、人工字段保留、库存快照失败保护及任务互斥。

官方接口依据与数据模型详见 [数据接入设计](docs/base-ingestion.md)。
