"""Local organization and ownership data; Base synchronization never writes these tables."""
import json
import sqlite3
import uuid

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.ingestion import encode, now

router = APIRouter(prefix="/api/management", tags=["员工与归属管理"])
SCHEMA = """
CREATE TABLE IF NOT EXISTS business_groups (
    id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS employees (
    id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name TEXT NOT NULL, group_id INTEGER REFERENCES business_groups(id),
    active INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS shop_ownership (
    platform TEXT NOT NULL, shop_id TEXT NOT NULL,
    employee_id INTEGER REFERENCES employees(id), group_id INTEGER REFERENCES business_groups(id),
    sales_owner TEXT, business_group TEXT, version INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL, PRIMARY KEY(platform, shop_id),
    FOREIGN KEY(platform, shop_id) REFERENCES base_shops(platform, shop_id)
);
CREATE TABLE IF NOT EXISTS ownership_history (
    id INTEGER PRIMARY KEY, entity_type TEXT NOT NULL, entity_key TEXT NOT NULL,
    batch_id TEXT NOT NULL, actor TEXT NOT NULL, reason TEXT NOT NULL,
    before_json TEXT NOT NULL, after_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ownership_history_entity ON ownership_history(entity_type,entity_key,id);
"""


def initialize_storage():
    from app.main import database
    with database() as db:
        db.executescript(SCHEMA)
        db.execute("BEGIN IMMEDIATE")
        # Additive migration retains all legacy annotations and source JSON.
        columns = {row["name"] for row in db.execute("PRAGMA table_info(order_annotations)")}
        for name, definition in [("employee_id", "INTEGER REFERENCES employees(id)"),
                                 ("group_id", "INTEGER REFERENCES business_groups(id)"),
                                 ("version", "INTEGER NOT NULL DEFAULT 0")]:
            if name not in columns:
                db.execute(f"ALTER TABLE order_annotations ADD COLUMN {name} {definition}")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GroupInput(StrictModel):
    code: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=100)
    active: bool = True
    actor: str = Field(default="本地操作员", min_length=1, max_length=100)
    reason: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def trim(self):
        self.name = self.name.strip()
        self.actor = self.actor.strip()
        if not self.name or not self.actor:
            raise ValueError("名称和操作人不能为空")
        return self


class EmployeeInput(GroupInput):
    group_id: int | None = Field(default=None, gt=0)


def reference(db, table, value, active=True):
    if value is None:
        return None
    row = db.execute(f"SELECT * FROM {table} WHERE id=?", (value,)).fetchone()
    if row is None:
        raise HTTPException(422, "员工或小组不存在。")
    if active and not row["active"]:
        raise HTTPException(422, "不能分配已停用的员工或小组。")
    return row


@router.get("/groups")
def groups():
    from app.main import database
    with database() as db:
        return [dict(row) for row in db.execute("SELECT * FROM business_groups ORDER BY id")]


@router.get("/employees")
def employees():
    from app.main import database
    with database() as db:
        return [dict(row) for row in db.execute("""SELECT e.*, g.name AS group_name FROM employees e
            LEFT JOIN business_groups g ON g.id=e.group_id ORDER BY e.id""")]


def save_organization(table, body, entity_id=None):
    from app.main import database
    try:
        with database() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(f"SELECT * FROM {table} WHERE id=?", (entity_id,)).fetchone()
            if entity_id is not None and existing is None:
                raise HTTPException(404, "员工或小组不存在。")
            values = body.model_dump(exclude={"actor", "reason"})
            if table == "employees":
                # Existing inactive memberships may be kept for historical records.
                reference(db, "business_groups", body.group_id, active=body.active)
            elif not body.active and db.execute("SELECT 1 FROM employees WHERE group_id=? AND active=1", (entity_id,)).fetchone():
                raise HTTPException(409, "请先调组或停用该小组内的在职员工。")
            values["updated_at"] = now()
            if existing:
                assignments = ",".join(f"{key}=?" for key in values)
                db.execute(f"UPDATE {table} SET {assignments} WHERE id=?", (*values.values(), entity_id))
            else:
                columns = ",".join(values)
                cursor = db.execute(f"INSERT INTO {table} ({columns}) VALUES ({','.join('?' for _ in values)})", tuple(values.values()))
                entity_id = cursor.lastrowid
            saved = dict(db.execute(f"SELECT * FROM {table} WHERE id=?", (entity_id,)).fetchone())
            before = dict(existing) if existing else {}
            comparable = lambda value: {key: item for key, item in value.items() if key != "updated_at"}
            if comparable(before) != comparable(saved):
                record_history(db, "employee" if table == "employees" else "group", str(entity_id),
                               before, saved, body.actor, body.reason, uuid.uuid4().hex)
            return saved
    except sqlite3.IntegrityError as error:
        raise HTTPException(409, "编号已存在（不区分大小写）。") from error


@router.post("/groups", status_code=201)
def create_group(body: GroupInput):
    return save_organization("business_groups", body)


@router.put("/groups/{entity_id}")
def update_group(entity_id: int, body: GroupInput):
    return save_organization("business_groups", body, entity_id)


@router.post("/employees", status_code=201)
def create_employee(body: EmployeeInput):
    return save_organization("employees", body)


@router.put("/employees/{entity_id}")
def update_employee(entity_id: int, body: EmployeeInput):
    return save_organization("employees", body, entity_id)


class Ownership(StrictModel):
    employee_id: int | None = Field(default=None, gt=0)
    group_id: int | None = Field(default=None, gt=0)
    extra_fields: dict | None = None


class OrderUpdate(Ownership):
    expected_version: int = Field(ge=0)
    actor: str = Field(min_length=1, max_length=100)
    reason: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def validate_update(self):
        self.actor = self.actor.strip()
        self.reason = self.reason.strip()
        if not self.actor or not {"employee_id", "group_id", "extra_fields"}.intersection(self.model_fields_set):
            raise ValueError("请填写操作人并选择要修改的归属字段")
        if "extra_fields" in self.model_fields_set and self.extra_fields is None:
            raise ValueError("附加字段必须为 JSON 对象")
        return self


class BulkUpdate(StrictModel):
    order_ids: list[int] = Field(min_length=1, max_length=500)
    expected_versions: dict[int, int]
    changes: Ownership
    actor: str = Field(min_length=1, max_length=100)
    reason: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def validate_batch(self):
        self.actor = self.actor.strip()
        self.reason = self.reason.strip()
        if not self.actor or any(value <= 0 for value in self.order_ids) or len(set(self.order_ids)) != len(self.order_ids):
            raise ValueError("订单 ID 必须为唯一正整数，操作人不能为空")
        if set(self.expected_versions) != set(self.order_ids) or any(value < 0 for value in self.expected_versions.values()):
            raise ValueError("每笔订单必须提供读取时的版本")
        if not self.changes.model_fields_set or ("extra_fields" in self.changes.model_fields_set and self.changes.extra_fields is None):
            raise ValueError("请选择有效的修改字段")
        return self


def annotation_state(db, order_id):
    row = db.execute("SELECT * FROM order_annotations WHERE order_id=?", (order_id,)).fetchone()
    if row is None:
        return {"employee_id": None, "group_id": None, "sales_owner": None, "business_group": None,
                "extra_fields": {}, "version": 0}
    return {"employee_id": row["employee_id"], "group_id": row["group_id"],
            "sales_owner": row["sales_owner"], "business_group": row["business_group"],
            "extra_fields": json.loads(row["extra_json"]), "version": row["version"]}


def resolve_ownership(db, before, changes):
    after = dict(before)
    if "employee_id" in changes:
        employee = reference(db, "employees", changes["employee_id"])
        after.update(employee_id=changes["employee_id"], sales_owner=employee["name"] if employee else None)
        if employee and "group_id" not in changes:
            changes = {**changes, "group_id": employee["group_id"]}
    if "group_id" in changes:
        group = reference(db, "business_groups", changes["group_id"])
        after.update(group_id=changes["group_id"], business_group=group["name"] if group else None)
    if {"employee_id", "group_id"}.intersection(changes) and after["employee_id"] is not None:
        employee = reference(db, "employees", after["employee_id"])
        if employee["group_id"] != after["group_id"]:
            raise HTTPException(422, "负责人不属于所选小组，请同时调整负责人或小组。")
    # Legacy free-text updates remain auditable and clear stale ID references.
    for name, id_name in [("sales_owner", "employee_id"), ("business_group", "group_id")]:
        if name in changes:
            after[name] = changes[name]
            after[id_name] = None
    if "extra_fields" in changes:
        try:
            raw = encode(changes["extra_fields"])
        except (TypeError, ValueError) as error:
            raise HTTPException(422, "附加字段必须是有效 JSON 对象。") from error
        if not isinstance(changes["extra_fields"], dict) or len(raw.encode()) > 65536:
            raise HTTPException(422, "附加字段必须是 JSON 对象，且不能超过 64 KiB。")
        after["extra_fields"] = changes["extra_fields"]
    return after


def record_history(db, kind, key, before, after, actor, reason, batch_id):
    db.execute("""INSERT INTO ownership_history(entity_type,entity_key,batch_id,actor,reason,
        before_json,after_json,created_at) VALUES(?,?,?,?,?,?,?,?)""",
               (kind, key, batch_id, actor, reason, encode(before), encode(after), now()))


def update_annotations(order_ids, changes, actor, reason, versions=None):
    from app.main import database
    batch_id = uuid.uuid4().hex
    results = []
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        for order_id in order_ids:
            if db.execute("SELECT 1 FROM base_orders WHERE order_id=?", (order_id,)).fetchone() is None:
                raise HTTPException(404, f"订单 {order_id} 不存在；本次未保存。")
            before = annotation_state(db, order_id)
            if versions is not None and versions[order_id] != before["version"]:
                raise HTTPException(409, f"订单 {order_id} 的归属已被修改，请重新读取；本次未保存。")
            after = resolve_ownership(db, before, changes)
            if after != before:
                after["version"] = before["version"] + 1
                db.execute("""INSERT INTO order_annotations
                    (order_id,sales_owner,business_group,extra_json,updated_at,employee_id,group_id,version)
                    VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(order_id) DO UPDATE SET
                    sales_owner=excluded.sales_owner,business_group=excluded.business_group,
                    extra_json=excluded.extra_json,updated_at=excluded.updated_at,
                    employee_id=excluded.employee_id,group_id=excluded.group_id,version=excluded.version""",
                           (order_id, after["sales_owner"], after["business_group"], encode(after["extra_fields"]),
                            now(), after["employee_id"], after["group_id"], after["version"]))
                record_history(db, "order", str(order_id), before, after, actor, reason, batch_id)
            results.append({"order_id": order_id, **after, "changed": after != before})
    return {"batch_id": batch_id, "changed": sum(result["changed"] for result in results), "orders": results}


@router.patch("/orders/{order_id}/ownership")
def update_order(order_id: int, body: OrderUpdate):
    changes = body.model_dump(include={"employee_id", "group_id", "extra_fields"}, exclude_unset=True)
    return update_annotations([order_id], changes, body.actor, body.reason, {order_id: body.expected_version})["orders"][0]


@router.post("/orders/ownership/batch")
def update_order_batch(body: BulkUpdate):
    return update_annotations(body.order_ids, body.changes.model_dump(exclude_unset=True),
                              body.actor, body.reason, body.expected_versions)


@router.get("/orders")
def orders(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
           platform: str | None = None, shop_id: str | None = None,
           employee_id: int | None = None, group_id: int | None = None, unassigned: bool = False):
    from app.main import database
    where, parameters = [], []
    for column, value in [("o.platform", platform), ("o.shop_id", shop_id), ("a.employee_id", employee_id), ("a.group_id", group_id)]:
        if value is not None:
            where.append(f"{column}=?"); parameters.append(value)
    if unassigned:
        where.append("(a.sales_owner IS NULL OR a.sales_owner='')")
    condition = " WHERE " + " AND ".join(where) if where else ""
    join = " FROM base_orders o LEFT JOIN order_annotations a ON a.order_id=o.order_id"
    with database() as db:
        total = db.execute("SELECT COUNT(*)" + join + condition, parameters).fetchone()[0]
        rows = db.execute("""SELECT o.order_id,o.platform,o.shop_id,o.created_at,
            a.sales_owner,a.business_group,a.employee_id,a.group_id,COALESCE(a.version,0) AS version"""
                          + join + condition + " ORDER BY o.order_id DESC LIMIT ? OFFSET ?", (*parameters, limit, offset))
        return {"total": total, "orders": [dict(row) for row in rows]}


@router.get("/orders/{order_id}/ownership")
def order_ownership(order_id: int):
    from app.main import database
    with database() as db:
        row = db.execute("SELECT platform,shop_id FROM base_orders WHERE order_id=?", (order_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "订单不存在。")
        defaults = db.execute("SELECT * FROM shop_ownership WHERE platform=? AND shop_id=?", tuple(row)).fetchone()
        return {"order_id": order_id, **annotation_state(db, order_id),
                "shop_defaults": dict(defaults) if defaults else None}


class ShopUpdate(OrderUpdate):
    @model_validator(mode="after")
    def disallow_extra(self):
        if "extra_fields" in self.model_fields_set:
            raise ValueError("店铺不支持订单附加字段")
        return self


@router.get("/shops")
def shops():
    from app.main import database
    with database() as db:
        return [dict(row) for row in db.execute("""SELECT s.platform,s.shop_id,s.name,s.active,
            a.employee_id,a.group_id,a.sales_owner,a.business_group,COALESCE(a.version,0) AS version
            FROM base_shops s LEFT JOIN shop_ownership a ON a.platform=s.platform AND a.shop_id=s.shop_id
            ORDER BY s.platform,s.shop_id""")]


@router.patch("/shops/{platform}/{shop_id}/ownership")
def update_shop(platform: str, shop_id: str, body: ShopUpdate):
    from app.main import database
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        shop = db.execute("SELECT * FROM base_shops WHERE platform=? AND shop_id=?", (platform, shop_id)).fetchone()
        if shop is None:
            raise HTTPException(404, "店铺不存在。")
        if not shop["active"]:
            raise HTTPException(422, "来源已移除的店铺不能配置新归属。")
        row = db.execute("SELECT * FROM shop_ownership WHERE platform=? AND shop_id=?", (platform, shop_id)).fetchone()
        before = ({key: row[key] for key in ["employee_id", "group_id", "sales_owner", "business_group", "version"]}
                  if row else {"employee_id": None, "group_id": None, "sales_owner": None, "business_group": None, "version": 0})
        if before["version"] != body.expected_version:
            raise HTTPException(409, "店铺归属已被修改，请重新读取。")
        after = resolve_ownership(db, before, body.model_dump(include={"employee_id", "group_id"}, exclude_unset=True))
        if before != after:
            after["version"] += 1
            db.execute("""INSERT INTO shop_ownership
                (platform,shop_id,employee_id,group_id,sales_owner,business_group,version,updated_at)
                VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(platform,shop_id) DO UPDATE SET
                employee_id=excluded.employee_id,group_id=excluded.group_id,sales_owner=excluded.sales_owner,
                business_group=excluded.business_group,version=excluded.version,updated_at=excluded.updated_at""",
                       (platform, shop_id, after["employee_id"], after["group_id"], after["sales_owner"],
                        after["business_group"], after["version"], now()))
            record_history(db, "shop", encode([platform, shop_id]), before, after, body.actor, body.reason, uuid.uuid4().hex)
        return {"platform": platform, "shop_id": shop_id, **after}


@router.get("/history")
def history(entity_type: str = Query(pattern="^(order|shop|employee|group)$"), order_id: int | None = None,
            platform: str | None = None, shop_id: str | None = None,
            entity_id: int | None = None,
            limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    from app.main import database
    if entity_type == "order" and order_id is not None:
        key = str(order_id)
    elif entity_type == "shop" and platform is not None and shop_id is not None:
        key = encode([platform, shop_id])
    elif entity_type in {"employee", "group"} and entity_id is not None:
        key = str(entity_id)
    else:
        raise HTTPException(422, "请指定订单 ID 或平台与店铺 ID。")
    with database() as db:
        total = db.execute("SELECT COUNT(*) FROM ownership_history WHERE entity_type=? AND entity_key=?", (entity_type, key)).fetchone()[0]
        rows = db.execute("SELECT * FROM ownership_history WHERE entity_type=? AND entity_key=? ORDER BY id DESC LIMIT ? OFFSET ?",
                          (entity_type, key, limit, offset))
        entries = []
        for row in rows:
            entry = dict(row)
            entry["before"] = json.loads(entry.pop("before_json"))
            entry["after"] = json.loads(entry.pop("after_json"))
            entries.append(entry)
        return {"total": total, "history": entries}
