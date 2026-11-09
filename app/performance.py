"""Local attribution rules and cross-platform comparisons; never writes Base."""
import json
import uuid
import hashlib
from datetime import date

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.ingestion import encode, now
from app.management import record_history, resolve_ownership

router = APIRouter(prefix="/api/performance", tags=["销售分析与归属规则"])
SCHEMA = """
CREATE TABLE IF NOT EXISTS sales_line_ownership (
    order_id INTEGER NOT NULL, position INTEGER NOT NULL, fingerprint TEXT NOT NULL,
    employee_id INTEGER, group_id INTEGER, sales_owner TEXT, business_group TEXT,
    rule_id INTEGER, rule_version INTEGER, version INTEGER NOT NULL, updated_at TEXT NOT NULL,
    PRIMARY KEY(order_id,position)
);
CREATE TABLE IF NOT EXISTS sales_assignment_rules (
    id INTEGER PRIMARY KEY, scope TEXT NOT NULL,
    platform TEXT NOT NULL, shop_id TEXT NOT NULL, source_sku TEXT NOT NULL,
    inventory_id INTEGER NOT NULL, node_id INTEGER REFERENCES company_products(id), valid_from TEXT NOT NULL, valid_to TEXT,
    employee_id INTEGER REFERENCES employees(id), group_id INTEGER REFERENCES business_groups(id),
    sales_owner TEXT, business_group TEXT, active INTEGER NOT NULL,
    version INTEGER NOT NULL, updated_at TEXT NOT NULL
);

"""


def initialize_storage():
    from app.main import database
    with database() as db:
        db.executescript(SCHEMA)


class RuleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int | None = Field(default=None, gt=0)
    scope: str = Field(pattern="^(shop|sku)$")
    platform: str = Field(default="", max_length=100)
    shop_id: str = Field(default="", max_length=100)
    source_sku: str = Field(default="", max_length=200)
    inventory_id: int = Field(default=0, ge=0)
    node_id: int | None = Field(default=None, gt=0)
    valid_from: date
    valid_to: date | None = None
    employee_id: int | None = Field(default=None, gt=0)
    group_id: int | None = Field(default=None, gt=0)
    active: bool = True
    expected_version: int = Field(ge=0)
    actor: str = Field(min_length=1, max_length=100)
    reason: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def validate_scope(self):
        for key in ("platform", "shop_id", "source_sku", "actor", "reason"):
            setattr(self, key, getattr(self, key).strip())
        if not self.actor or (self.valid_to and self.valid_to < self.valid_from):
            raise ValueError("操作人不能为空，结束日期不能早于开始日期")
        if self.shop_id and not self.platform:
            raise ValueError("指定店铺时必须同时指定平台")
        if self.scope == "shop" and (not self.shop_id or self.source_sku or self.inventory_id or self.node_id):
            raise ValueError("店铺规则需指定平台与店铺，不填写 SKU 或目录")
        if self.scope == "sku" and bool(self.source_sku) == bool(self.node_id):
            raise ValueError("SKU 规则请选择公司产品节点或填写精确来源 SKU，二选一")
        if not (self.employee_id or self.group_id):
            raise ValueError("请指定负责人或小组")
        return self


@router.get("/rules")
def rules():
    from app.main import database
    with database() as db:
        return [dict(r) for r in db.execute("SELECT * FROM sales_assignment_rules ORDER BY id DESC")]


@router.put("/rules")
def save_rule(body: RuleInput):
    from app.main import database
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        old = db.execute("SELECT * FROM sales_assignment_rules WHERE id=?", (body.id,)).fetchone()
        if body.id and old is None:
            raise HTTPException(404, "规则不存在。")
        if (old["version"] if old else 0) != body.expected_version:
            raise HTTPException(409, "规则已被修改，请重新读取。")
        if body.platform and not db.execute("SELECT 1 FROM base_platforms WHERE platform=? UNION SELECT 1 FROM base_shops WHERE platform=?", (body.platform, body.platform)).fetchone():
            raise HTTPException(422, "平台不存在。")
        if body.shop_id and not db.execute("SELECT 1 FROM base_shops WHERE platform=? AND shop_id=? AND active=1", (body.platform, body.shop_id)).fetchone():
            raise HTTPException(422, "店铺不存在或已停用。")
        if body.inventory_id and not db.execute("SELECT 1 FROM base_catalogs WHERE inventory_id=? AND active=1", (body.inventory_id,)).fetchone():
            raise HTTPException(422, "库存目录不存在或已停用。")
        if body.node_id and not db.execute("SELECT 1 FROM company_products WHERE id=? AND active=1", (body.node_id,)).fetchone():
            raise HTTPException(422, "公司产品节点不存在或停用。")
        if body.active:
            overlaps = db.execute("""SELECT id FROM sales_assignment_rules
                WHERE scope=? AND platform=? AND shop_id=? AND source_sku=? AND inventory_id=?
                AND COALESCE(node_id,0)=? AND active=1 AND id!=? AND valid_from<=? AND COALESCE(valid_to,'9999-12-31')>=?""",
                (body.scope, body.platform, body.shop_id, body.source_sku, body.inventory_id,
                 body.node_id or 0, body.id or 0, str(body.valid_to or date.max), str(body.valid_from))).fetchone()
            if overlaps:
                raise HTTPException(409, "相同范围的生效日期重叠，请先调整已有规则。")
        if old and not body.active and body.employee_id == old['employee_id'] and body.group_id == old['group_id']:
            ownership = {key: old[key] for key in ('employee_id','group_id','sales_owner','business_group')}
        else:
            ownership = resolve_ownership(db, {}, {"employee_id": body.employee_id} |
                ({"group_id": body.group_id} if body.group_id is not None else {}))
        values = {key: value for key, value in body.model_dump(mode="json").items()
                  if key not in {"id", "expected_version", "actor", "reason", "employee_id", "group_id"}}
        values.update({key: ownership.get(key) for key in ("employee_id", "group_id", "sales_owner", "business_group")})
        before = dict(old) if old else {}
        if old and all(old[key] == value for key, value in values.items()):
            return before
        values.update(version=body.expected_version + 1, updated_at=now())
        if old:
            db.execute("UPDATE sales_assignment_rules SET " + ",".join(key+"=?" for key in values) + " WHERE id=?",
                       (*values.values(), body.id))
            rule_id = body.id
        else:
            cursor = db.execute("INSERT INTO sales_assignment_rules (" + ",".join(values) +
                ") VALUES (" + ",".join("?" for _ in values) + ")", tuple(values.values()))
            rule_id = cursor.lastrowid
        saved = dict(db.execute("SELECT * FROM sales_assignment_rules WHERE id=?", (rule_id,)).fetchone())
        record_history(db, "sales_rule", str(rule_id), before, saved, body.actor, body.reason, uuid.uuid4().hex)
        return saved


def resolve_line_owner(order, item, day, rules, basis, product_path=()):
    keys = ("employee_id", "group_id", "sales_owner", "business_group")
    saved = {key: order[key] for key in keys}
    snapshot = item.get('_owner')
    if snapshot and snapshot['rule_id'] is None and snapshot['fingerprint'] == line_fingerprint(item):
        return {**{key: snapshot[key] for key in keys}, 'ownership_source': 'line_manual',
                'rule_id': None, 'rule_version': None}
    locked = json.loads(order["extra_json"] or "{}").get("ownership_locked") is True
    if any(value is not None and value != "" for value in saved.values()) or locked:
        return {**saved, "ownership_source": "order", "rule_id": None, "rule_version": None}
    if snapshot and snapshot['fingerprint'] == line_fingerprint(item):
        return {**{key: snapshot[key] for key in keys}, 'ownership_source': 'snapshot',
                'rule_id': snapshot['rule_id'], 'rule_version': snapshot['rule_version']}
    if basis != "rules":
        return {**saved, "ownership_source": "unassigned", "rule_id": None, "rule_version": None}
    matches = []
    for rule in rules:
        if not rule["active"] or day < rule["valid_from"] or (rule["valid_to"] and day > rule["valid_to"]):
            continue
        if rule["platform"] and rule["platform"] != order["platform"]:
            continue
        if rule["shop_id"] and rule["shop_id"] != order["shop_id"]:
            continue
        if rule.get("node_id") and rule["node_id"] not in product_path:
            continue
        if rule["scope"] == "sku" and rule["source_sku"] and rule["source_sku"] != item["sku"]:
            continue
        if rule["inventory_id"] and (item["storage"] != "db" or str(rule["inventory_id"]) != str(item["storage_id"])):
            continue
        matches.append(rule)
    if not matches:
        return {**saved, "ownership_source": "unassigned", "rule_id": None, "rule_version": None}
    # Most specific SKU scope first, then dated shop rules; never use live shop defaults.
    score = lambda r: (r["scope"] == "sku", bool(r["shop_id"]), bool(r["platform"]), bool(r["inventory_id"]), product_path.index(r["node_id"])+1 if r.get("node_id") in product_path else len(product_path)+1 if r['source_sku'] else 0)
    winner = max(matches, key=score)
    return {**{key: winner[key] for key in keys}, "ownership_source": "rule",
            "rule_id": winner["id"], "rule_version": winner["version"]}


def line_fingerprint(item):
    fields = [str(item.get(key) or '') for key in ('order_product_id', 'storage', 'storage_id', 'product_id', 'variant_id', 'sku')]
    return hashlib.sha256(encode(fields).encode()).hexdigest()


def save_line_snapshot(db, item, owner, actor, reason):
    old = db.execute('SELECT * FROM sales_line_ownership WHERE order_id=? AND position=?', (item['order_id'], item['position'])).fetchone()
    db.execute('''INSERT INTO sales_line_ownership VALUES(?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(order_id,position) DO UPDATE SET fingerprint=excluded.fingerprint,
        employee_id=excluded.employee_id,group_id=excluded.group_id,sales_owner=excluded.sales_owner,
        business_group=excluded.business_group,rule_id=excluded.rule_id,rule_version=excluded.rule_version,
        version=excluded.version,updated_at=excluded.updated_at''',
        (item['order_id'], item['position'], line_fingerprint(item), owner['employee_id'], owner['group_id'],
         owner['sales_owner'], owner['business_group'], owner['rule_id'], owner['rule_version'],
         old['version']+1 if old else 1, now()))
    saved = dict(db.execute('SELECT * FROM sales_line_ownership WHERE order_id=? AND position=?', (item['order_id'], item['position'])).fetchone())
    record_history(db, 'line_owner', encode([item['order_id'], item['position']]), dict(old) if old else {}, saved, actor, reason, uuid.uuid4().hex)


def assign_new_order(db, order_id):
    """Freeze rules for newly discovered orders only, inside the ingestion transaction."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    import os
    from app.catalog import read_catalog, sales_node, ancestry
    raw = db.execute('SELECT o.*,a.employee_id,a.group_id,a.sales_owner,a.business_group,a.extra_json FROM base_orders o LEFT JOIN order_annotations a ON a.order_id=o.order_id WHERE o.order_id=?', (order_id,)).fetchone()
    try:
        day = datetime.fromtimestamp(raw['created_at'], ZoneInfo(os.environ.get('SALES_ASSIGNMENT_TIMEZONE', 'Asia/Shanghai'))).date().isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return
    rules = [dict(r) for r in db.execute('SELECT * FROM sales_assignment_rules WHERE active=1')]
    nodes, mapping = read_catalog(db)
    for raw_item in db.execute('SELECT * FROM base_order_items WHERE order_id=?', (order_id,)):
        item = dict(raw_item)
        source_id = item['variant_id'] if item['variant_id'] not in (None, '', '0') else item['product_id']
        native = db.execute('SELECT * FROM base_products WHERE inventory_id=? AND product_id=?', (item['storage_id'], source_id)).fetchone() if item['storage'] == 'db' else None
        if native and source_id != item['product_id'] and str(native['parent_id']) != item['product_id']:
            native = None
        identity = sales_node(mapping, raw, item, native)
        owner = resolve_line_owner(raw, item, day, rules, 'rules', ancestry(nodes, identity))
        if owner['ownership_source'] == 'rule':
            save_line_snapshot(db, item, owner, 'Base 同步', '新订单按生效规则记录明细归属；日期采用 Base 创建时间')


class MaterializeInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    filters: dict
    expected_token: str | None = None
    actor: str = Field(min_length=1, max_length=100)
    reason: str = Field(default='', max_length=500)


@router.get('/lines/{order_id}/{position}')
def line_owner(order_id: int, position: int):
    from app.main import database
    with database() as db:
        item = db.execute('SELECT * FROM base_order_items WHERE order_id=? AND position=?', (order_id, position)).fetchone()
        if item is None:
            raise HTTPException(404, '商品明细不存在。')
        saved = db.execute('SELECT * FROM sales_line_ownership WHERE order_id=? AND position=?', (order_id, position)).fetchone()
        valid = saved is not None and saved['fingerprint'] == line_fingerprint(dict(item))
        return {'order_id': order_id, 'position': position, 'source_sku': item['sku'],
                'fingerprint': line_fingerprint(dict(item)), 'version': saved['version'] if saved else 0,
                'employee_id': saved['employee_id'] if valid else None, 'group_id': saved['group_id'] if valid else None,
                'snapshot_valid': valid}


class LineOwnerInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    employee_id: int | None = Field(default=None, gt=0)
    group_id: int | None = Field(default=None, gt=0)
    expected_version: int = Field(ge=0)
    expected_fingerprint: str = Field(min_length=64, max_length=64)
    actor: str = Field(min_length=1, max_length=100)
    reason: str = Field(default='', max_length=500)


@router.put('/lines/{order_id}/{position}')
def update_line_owner(order_id: int, position: int, body: LineOwnerInput):
    from app.main import database
    if not body.actor.strip():
        raise HTTPException(422, '操作人不能为空。')
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        item = db.execute('SELECT * FROM base_order_items WHERE order_id=? AND position=?', (order_id, position)).fetchone()
        old = db.execute('SELECT * FROM sales_line_ownership WHERE order_id=? AND position=?', (order_id, position)).fetchone()
        if item is None:
            raise HTTPException(404, '商品明细不存在。')
        item = dict(item)
        if line_fingerprint(item) != body.expected_fingerprint or (old['version'] if old else 0) != body.expected_version:
            raise HTTPException(409, '商品明细或归属已变化，请重新读取。')
        owner = resolve_ownership(db, dict(employee_id=None,group_id=None,sales_owner=None,business_group=None), {'employee_id': body.employee_id} | ({'group_id': body.group_id} if body.group_id is not None else {}))
        owner.update(rule_id=None, rule_version=None)
        save_line_snapshot(db, item, owner, body.actor.strip(), body.reason)
    return {'saved': True}


@router.post('/materialize')
def materialize(body: MaterializeInput):
    from app.main import database
    from app.reports import ReportFilters, report_lines
    from pydantic import ValidationError
    if not body.actor.strip():
        raise HTTPException(422, '操作人不能为空。')
    try:
        filters = ReportFilters.model_validate({**body.filters, 'ownership_basis': 'rules'})
    except ValidationError as error:
        raise HTTPException(422, '请检查试算日期及筛选条件。') from error
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        lines, _, _ = report_lines(filters)
        candidates = [r for r in lines if r['ownership_source'] == 'rule']
        if len(candidates) > 500:
            raise HTTPException(422, '单次最多保存 500 条明细，请缩小日期范围。')
        entries = []
        for row in candidates:
            item = dict(db.execute('SELECT * FROM base_order_items WHERE order_id=? AND position=?', (row['order_id'], row['position'])).fetchone())
            entries.append((row, item))
        token = hashlib.sha256(encode([filters.model_dump(mode='json'), [(r, line_fingerprint(i)) for r, i in entries]]).encode()).hexdigest()
        if body.expected_token is not None:
            if body.expected_token != token:
                raise HTTPException(409, '数据或规则已变化，请重新预览后保存。')
            for row, item in entries:
                save_line_snapshot(db, item, row, body.actor.strip(), body.reason)
        return {'token': token, 'line_count': len(entries), 'total_units': sum(r['quantity'] for r in candidates),
                'saved': body.expected_token is not None,
                'lines': [{key: r[key] for key in ('order_id','position','source_sku','quantity','business_group','sales_owner','rule_id','rule_version')} for r in candidates]}


def comparisons(lines):
    def bucket(target, key, row, label):
        value = target.setdefault(key, {"name": label, "quantity": 0, "_orders": set(), "daily": {}, "children": {}})
        value["quantity"] += row["quantity"]
        value["_orders"].add(row["order_id"])
        value["daily"][row["date"]] = value["daily"].get(row["date"], 0) + row["quantity"]
        return value
    platforms, products, teams, hierarchy = {}, {}, {}, {}
    for row in lines:
        branch = hierarchy
        for index, identity in enumerate(row.get('company_path', [])):
            node = bucket(branch, identity, row, row['company_path_labels'][index])
            node['node_id'] = identity
            branch = node['children']
        shop_key = (row["platform"], row["shop_id"])
        p = bucket(platforms, row["platform"], row, row["platform_name"])
        p["platform"] = row["platform"]
        s = bucket(p["children"], shop_key, row, row["shop_name"])
        s.update(platform=row["platform"], shop_id=row["shop_id"])
        product = bucket(products, tuple(row["key"]), row, row["label"])
        product.update(key=list(row["key"]), parent_sku=row["parent_sku"])
        pp = bucket(product["children"], row["platform"], row, row["platform_name"])
        pp["platform"] = row["platform"]
        ps = bucket(pp["children"], shop_key, row, row["shop_name"])
        ps.update(platform=row["platform"], shop_id=row["shop_id"])
        group_key = ("id", row["group_id"]) if row["group_id"] is not None else ("text", row["business_group"])
        employee_key = ("id", row["employee_id"]) if row["employee_id"] is not None else ("text", row["sales_owner"])
        team = bucket(teams, group_key, row, row["business_group"] or "未分配")
        team.update(identity_type=group_key[0], id=group_key[1])
        employee = bucket(team["children"], employee_key, row, row["sales_owner"] or "未分配")
        employee.update(identity_type=employee_key[0], id=employee_key[1])
    def finish(values, total):
        result = []
        for value in values.values():
            value["order_count"] = len(value.pop("_orders"))
            value["share"] = round(value["quantity"] * 100 / total, 2) if total else 0
            value["children"] = finish(value["children"], value["quantity"])
            result.append(value)
        return sorted(result, key=lambda r: (-r["quantity"], r["name"]))
    total = sum(row["quantity"] for row in lines)
    return {"platforms": finish(platforms, total), "products": finish(products, total), "teams": finish(teams, total),
            'hierarchy': finish(hierarchy, total)}


@router.get("/history")
def history(kind: str = Query(pattern="^(sales_rule|line_owner|product|product_link|stock_allocation)$"), key: str = Query(min_length=1),
            offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
    from app.main import database
    if kind in ('line_owner', 'stock_allocation', 'product_link'):
        try:
            identity = json.loads(key)
            if not isinstance(identity, list):
                raise ValueError()
            key = encode(identity)
        except (ValueError, TypeError):
            raise HTTPException(422, '历史对象标识格式不正确。')
    with database() as db:
        total = db.execute("SELECT COUNT(*) FROM ownership_history WHERE entity_type=? AND entity_key=?", (kind, key)).fetchone()[0]
        rows = []
        for raw in db.execute("SELECT * FROM ownership_history WHERE entity_type=? AND entity_key=? ORDER BY id DESC LIMIT ? OFFSET ?", (kind, key, limit, offset)):
            row = dict(raw)
            row["before"] = json.loads(row.pop("before_json"))
            row["after"] = json.loads(row.pop("after_json"))
            rows.append(row)
    return {"total": total, "history": rows}


def ownership_identity_filter(raw, id_field, text_field):
    if raw is None:
        return lambda row: True
    try:
        identity = json.loads(raw)
        if not isinstance(identity, list) or len(identity) != 2:
            raise ValueError()
        kind, value = identity
        if kind == "id" and type(value) is int and value > 0:
            return lambda row: row[id_field] == value
        if kind == "text" and (value is None or isinstance(value, str)):
            return lambda row: row[id_field] is None and row[text_field] == value
        raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(422, "归属维度键格式不正确。")

