# 第一阶段：员工、小组和归属管理

## 业务规则

- 小组和员工使用不区分大小写的唯一编号，员工可属于一个小组或暂不分组。支持编辑、启用和停用，不提供物理删除。
- 停用小组前先调组或停用其在职员工。已停用员工、小组及已被 Base 移除的店铺不可分配新归属；原历史记录保留。
- 店铺归属以 `platform + shop_id` 识别，保存默认销售负责人和业务小组。默认归属是供人工应用的建议，不是会追溯改写历史订单的动态关联。
- 单笔订单可载入店铺建议，确认保存后才建立人工归属。批量根据页面筛选选择订单，也可由 API 一次提交最多 500 个明确 ID。
- 指定员工但省略小组字段时，采用该员工当前小组。显式指定小组时必须与员工当前小组一致。仅修改附加字段不强制迁移历史员工/小组，停用和调组不改写旧归属。
- `employee_id: null` 清空负责人，`group_id: null` 清空小组；存在负责人时单独清空其非空小组会被拒绝，可同时清空二者。未传的字段保留，附加字段传入时整体替换（最多 64 KiB）。
- 订单保存员工、小组 ID 和名称快照。员工/小组后来改名、调组不修改订单快照，避免历史业绩归属无记录地改变。
- 每次实际变化记录前后值、版本、操作人、原因、时间、批次；重复保存相同内容不制造新历史。员工调组、启停及店铺默认归属更改同样有历史。操作人是手工声明，不是已经认证的登录用户。
- Base 产品、库存、店铺与订单同步逻辑保留；只写源数据表，不覆盖员工、小组、店铺人工归属、订单人工归属或历史表。

## 数据迁移与升级

新增 `business_groups`、`employees`、`shop_ownership` 和 `ownership_history`。向 `order_annotations` 添加可空的员工、小组 ID 和默认 0 的版本。原文本、附加字段、时间和源数据均保留，不猜测同名人员，不自动创建员工或批量分配。

启动初始化采用幂等增量迁移，并在迁移字段时加 SQLite 写锁，供网页和 worker 同时启动。升级前先停止旧网页和 worker、备份数据库，再用 `bash scripts/start-all.sh` 启动新版本。老进程的旧 SQL 不应继续写入升级后的表。跨新旧版本回退应使用备份，不能直接让旧程序操作新增列后的表。

## API

| 接口 | 用途 |
| --- | --- |
| `GET/POST /api/management/groups` | 小组列表 / 创建 |
| `PUT /api/management/groups/{id}` | 完整编辑小组（含停用） |
| `GET/POST /api/management/employees` | 员工列表 / 创建 |
| `PUT /api/management/employees/{id}` | 完整编辑员工、所属小组和启停状态 |
| `GET /api/management/shops` | 店铺和人工默认归属 |
| `PATCH /api/management/shops/{platform}/{shop_id}/ownership` | 更新店铺默认归属，带版本保护 |
| `GET /api/management/orders` | 分页订单，按平台、店铺、员工、小组或未分配筛选 |
| `GET /api/management/orders/{id}/ownership` | 单笔归属、版本、附加字段及店铺建议 |
| `PATCH /api/management/orders/{id}/ownership` | 单笔更新 |
| `POST /api/management/orders/ownership/batch` | 整批事务更新 |
| `GET /api/management/history` | 分页读取订单、店铺、员工或小组历史 |

单笔更新示例（先读取版本）：

```json
{"employee_id": 1, "expected_version": 0, "actor": "操作人", "reason": "交接"}
```

批量更新示例：

```json
{
  "order_ids": [101, 102],
  "expected_versions": {"101": 0, "102": 3},
  "changes": {"employee_id": 1},
  "actor": "操作人",
  "reason": "统一分配"
}
```

批量禁止重复 ID，必须提供每笔读取时的版本，存在缺失订单、无效归属或版本冲突时没有任何一笔或历史记录被保存。版本冲突返回 409，重新读取并审查差异后再提交；不自动覆盖他人修改。新页面单笔加载会丢弃过期请求响应，避免快速切换订单时错用前一个订单的内容。

历史查询：`entity_type=order&order_id=101`，或 `entity_type=shop&platform=amazon&shop_id=1`；员工、小组使用 `entity_type=employee|group&entity_id=1`。支持 `limit`（最多 200）和 `offset`。

旧 `/api/base/orders/{id}/annotation` 文本接口继续可用，写入也计入版本和历史；操作人标记“兼容接口”，不提供新接口的读取版本校验。旧文本变更会清空对应员工/小组 ID。新业务开发应使用管理接口。

本阶段未实现账号登录、角色权限、审批或业绩核算报表；保持单用户回环地址运行。历史是本地操作日志，不代表具备不可篡改的外部审计能力。
