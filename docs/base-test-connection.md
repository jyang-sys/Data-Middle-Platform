# 独立 Base 测试数据源

## 配置与隔离

测试账号使用独立的 `data/environments/base-test/platform.sqlite3`。本地服务配置为同目录 `runtime.json`（权限 0600、Git 忽略），包含 `BASELINKER_API_TOKEN`、`DATA_PLATFORM_DB` 和 `BASE_DATA_ENVIRONMENT=test`。文档、浏览器、Git 与部署包不保存真实密钥。

线上私有 Site 使用原生运行时 Secret `BASELINKER_TEST_API_TOKEN`，与开发环境原有账号的凭据分开。修改运行时 Secret 后需部署已保存的站点版本才能应用。站点仍保持原访问范围，未开放公开访问。

```bash
# 从 Base 全量核对来源数据；只读上游
.venv/bin/python scripts/run-test-environment.py sync
# 启动独立测试接口
.venv/bin/python scripts/run-test-environment.py web --port 8003
# 可选：持续同步进程，仍使用独立测试数据库
.venv/bin/python scripts/run-test-environment.py worker
```

来源产品可通过 `import-base-product-tree.py --db <测试数据库路径>` 初始化参考树，只使用明确的目录/产品 ID 和父子关系，不按名称推断公司型号、颜色或跨目录同规格。已有人工映射不重复建立，不覆盖人工移动或归属；循环关系拒绝导入。参考编码 `BASE-目录-产品ID` 与原 SKU 分开保留，后续需要业务维护统一产品。

## 在线刷新

私有 Site 从静态演示升级为小规模测试用 Worker。首页“↻”、库存和产品刷新会调用同源服务端接口，服务端使用运行时 Secret 只调用 `getOrderSources/getInventories/getInventoryWarehouses/getInventoryProductsList/getInventoryProductsData/getOrders`，不调用 Base 写接口。

刷新读取完整可访问订单及产品、库存后一次性发布新快照。失败保留上次成功缓存和时间，不能以空数据替代。缓存按密钥哈希隔离，浏览器仅接收筛选所需字段，不接收密钥、客户地址或完整订单 JSON。默认打开使用最近成功数据，点击刷新重新向 Base 核对；不是后台定时同步。

测试模式默认包含未确认订单，便于核对新建测试单，页面标明试算及确认范围。正式报表默认确认口径不因此改变。原始平台日期仍需核对，不能把 Base 创建日期认定为原平台日期。借货/虚拟记账等排除规则尚未维护时不按 SKU 名称自动排除订单。

线上读源刷新保留打包时的公司映射、分组和库存份额参考。中台本地维护这些业务配置后需重新发布参考数据；刷新 Base 不会自动猜测销售人员、销售组、货权或实物口径。当前未配置的小组显示未分配，未确认类型的库存不合计成实物。

## 打包

```bash
.venv/bin/python scripts/build-preview.py --live-url http://127.0.0.1:8003 --output data/environments/base-test/snapshot.html
.venv/bin/python scripts/build-base-test-site.py --snapshot data/environments/base-test/snapshot.html --db data/environments/base-test/platform.sqlite3 --site-directory /workspace/sites/sales-inventory-demo
```

构建器要求数据标记 `base_test`，保留既有 `project_id`，将页面、无客户资料的业务参考和 Worker 编译到 `dist/server/index.js`，并拒绝密钥出现在部署文件中。更新 manifest 为 Worker 入口，不修改访问策略。部署通过原生 Sites 私有发布流程执行。

## 验证与边界

- 测试账号 API 实际连通并完成四类来源同步；初次读取 28 个产品、42 条库存、5 笔订单和 10 个来源店铺，其中 4 笔未确认。
- Worker 投影与本地源库的订单明细、数量、日期、确认标志和库存数量一致。
- 自动测试覆盖只读方法、分页、刷新缓存、源异常、同源校验、未确认订单、未知库存、客户资料和密钥不外泄。
- 浏览器用受控来源验证修改后数量刷新、异常时保留上次数据和同步时间；测试没有修改 Base 订单或库存。

该 Worker 用于当前小规模私有测试，按完整目录/订单扫描，上游 API 访问做进程内节流，不保证多实例全局请求预算。规模扩大需改为持久任务与统一限流。正式权限管理、业务配置写入与审批继续由中台服务负责；该 Site 的刷新不提供上游写回。
