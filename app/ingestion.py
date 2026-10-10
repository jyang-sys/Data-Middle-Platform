"""Read-only Base ingestion, isolated source records and local order annotations."""
import json
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.sales import api_request, amount

router = APIRouter(prefix="/api/base", tags=["Base 数据接入"])
DOMAINS = ("shops", "products", "inventory", "orders")
SCHEMA = """
CREATE TABLE IF NOT EXISTS base_api_budget (id INTEGER PRIMARY KEY CHECK(id=1), next_call REAL NOT NULL);
CREATE TABLE IF NOT EXISTS base_platforms (
    platform TEXT PRIMARY KEY, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS base_shops (
    platform TEXT NOT NULL, shop_id TEXT NOT NULL, name TEXT NOT NULL,
    raw_json TEXT NOT NULL, updated_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY(platform, shop_id)
);
CREATE TABLE IF NOT EXISTS base_catalogs (
    inventory_id INTEGER PRIMARY KEY, name TEXT NOT NULL, raw_json TEXT NOT NULL,
    updated_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS base_warehouses (
    warehouse_key TEXT PRIMARY KEY, warehouse_type TEXT NOT NULL,
    internal_id INTEGER, name TEXT NOT NULL, raw_json TEXT NOT NULL,
    updated_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS base_products (
    inventory_id INTEGER NOT NULL, product_id INTEGER NOT NULL,
    parent_id INTEGER NOT NULL, sku TEXT NOT NULL, name TEXT NOT NULL,
    raw_json TEXT NOT NULL, updated_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY(inventory_id, product_id)
);
CREATE INDEX IF NOT EXISTS base_product_sku ON base_products(sku);
CREATE INDEX IF NOT EXISTS base_product_parent ON base_products(inventory_id, parent_id);
CREATE TABLE IF NOT EXISTS base_stock (
    inventory_id INTEGER NOT NULL, product_id INTEGER NOT NULL,
    warehouse_key TEXT NOT NULL, stock TEXT, reservations TEXT, incoming TEXT,
    raw_json TEXT NOT NULL, fetched_at TEXT NOT NULL,
    PRIMARY KEY(inventory_id, product_id, warehouse_key)
);
CREATE TABLE IF NOT EXISTS base_orders (
    order_id INTEGER PRIMARY KEY, external_order_id TEXT,
    platform TEXT NOT NULL, shop_id TEXT NOT NULL, created_at INTEGER,
    confirmed_at INTEGER, confirmed INTEGER NOT NULL,
    status_id INTEGER, currency TEXT, raw_json TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS base_order_created ON base_orders(created_at);
CREATE TABLE IF NOT EXISTS base_order_items (
    order_id INTEGER NOT NULL REFERENCES base_orders(order_id), position INTEGER NOT NULL,
    order_product_id TEXT, storage TEXT, storage_id TEXT,
    product_id TEXT, variant_id TEXT, sku TEXT, quantity TEXT NOT NULL,
    raw_json TEXT NOT NULL, PRIMARY KEY(order_id, position)
);
CREATE TABLE IF NOT EXISTS order_annotations (
    order_id INTEGER PRIMARY KEY REFERENCES base_orders(order_id),
    sales_owner TEXT, business_group TEXT, extra_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS base_sync_jobs (
    id TEXT PRIMARY KEY, domain TEXT NOT NULL, status TEXT NOT NULL,
    started_at TEXT NOT NULL, finished_at TEXT, records INTEGER NOT NULL DEFAULT 0,
    error TEXT, cursor INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS base_one_sync ON base_sync_jobs(domain) WHERE status='running';
"""


def now():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def initialize_storage():
    from app.main import database
    with database() as db:
        db.executescript(SCHEMA)


def throttle():
    from app.main import database
    # A durable shared budget covers API and worker processes using this database.
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT OR IGNORE INTO base_api_budget VALUES(1,0)")
        scheduled = max(time.time(), db.execute("SELECT next_call FROM base_api_budget WHERE id=1").fetchone()[0])
        db.execute("UPDATE base_api_budget SET next_call=? WHERE id=1", (scheduled + 0.72,))
    delay = scheduled - time.time()
    if delay > 0:
        time.sleep(delay)


def call(method, parameters):
    return api_request(method, parameters)


def mapping(payload, key):
    result = payload.get(key)
    if result == []:  # PHP empty associative arrays may encode as [].
        return {}
    if not isinstance(result, dict):
        raise HTTPException(502, f"Base 返回的 {key} 格式不正确。")
    return result


def sequence(payload, key):
    result = payload.get(key)
    if not isinstance(result, list) or any(not isinstance(x, dict) for x in result):
        raise HTTPException(502, f"Base 返回的 {key} 格式不正确。")
    return result


@contextmanager
def sync_job(domain):
    import sqlite3
    from app.main import database
    job_id = uuid.uuid4().hex
    # A database lease prevents overlap across worker and API processes.
    with database() as db:
        try:
            db.execute("INSERT INTO base_sync_jobs(id,domain,status,started_at) VALUES(?,?,'running',?)",
                       (job_id, domain, now()))
        except sqlite3.IntegrityError as error:
            raise HTTPException(409, "该数据域已有同步任务正在运行。") from error
    try:
        yield job_id
    except Exception as error:
        with database() as db:
            # Never persist upstream payloads or exceptions containing credentials.
            db.execute("UPDATE base_sync_jobs SET status='failed',finished_at=?,error=? WHERE id=?",
                       (now(), "同步失败，请检查网络、字段格式及 API 权限。", job_id))
        if isinstance(error, HTTPException):
            raise
        raise HTTPException(502, "同步失败，已保存页面可保留，任务未标记成功。") from error
    else:
        with database() as db:
            db.execute("UPDATE base_sync_jobs SET status='success',finished_at=? WHERE id=?", (now(), job_id))


def progress(db, job_id, count, cursor=0):
    db.execute("UPDATE base_sync_jobs SET records=records+?,cursor=? WHERE id=?", (count, cursor, job_id))


def sync_shops(job_id):
    from app.main import database
    sources = mapping(call("getOrderSources", {}), "sources")
    rows = []
    timestamp = now()
    for platform, shops in sources.items():
        if isinstance(shops, list):
            shops = dict(enumerate(shops))
        if not isinstance(shops, dict):
            raise ValueError("invalid shops")
        for shop_id, name in shops.items():
            if not isinstance(name, str):
                raise ValueError("invalid shop name")
            rows.append((platform, str(shop_id), name, encode({"name": name}), timestamp))
    with database() as db:
        db.execute("UPDATE base_shops SET active=0")
        for platform in sources:
            db.execute("INSERT INTO base_platforms VALUES(?,?) ON CONFLICT(platform) DO UPDATE SET updated_at=excluded.updated_at",
                       (platform, timestamp))
        db.executemany("""INSERT INTO base_shops(platform,shop_id,name,raw_json,updated_at) VALUES(?,?,?,?,?)
            ON CONFLICT(platform,shop_id) DO UPDATE SET name=excluded.name,raw_json=excluded.raw_json,
            updated_at=excluded.updated_at,active=1""", rows)
        progress(db, job_id, len(rows))


def fetch_catalogs():
    return sequence(call("getInventories", {}), "inventories")


def fetch_products(inventory_id):
    products = {}
    for page in range(1, 10002):
        batch = mapping(call("getInventoryProductsList", {
            "inventory_id": inventory_id, "page": page, "include_variants": True,
            "filter_sort": "id ASC"}), "products")
        if not batch:
            return products
        if page > 10000 or not (set(batch) - set(products)):
            raise ValueError("product pagination did not advance")
        products.update(batch)
        # Request details in small chunks; preserve all returned detail fields.
        ids = [int(key) for key in batch]
        for offset in range(0, len(ids), 100):
            details = mapping(call("getInventoryProductsData", {
                "inventory_id": inventory_id, "products": ids[offset:offset + 100]}), "products")
            if set(details) != {str(x) for x in ids[offset:offset + 100]}:
                raise ValueError("missing product detail")
            for key, value in details.items():
                products[key] = {**products[key], **value}
    raise ValueError("product pagination limit")


def upsert_catalogs(db, catalogs, timestamp):
    db.execute("UPDATE base_catalogs SET active=0")
    for catalog in catalogs:
        db.execute("""INSERT INTO base_catalogs(inventory_id,name,raw_json,updated_at) VALUES(?,?,?,?)
            ON CONFLICT(inventory_id) DO UPDATE SET name=excluded.name,raw_json=excluded.raw_json,
            updated_at=excluded.updated_at,active=1""",
                   (int(catalog["inventory_id"]), str(catalog.get("name", "")), encode(catalog), timestamp))


def sync_products(job_id):
    from app.main import database
    catalogs = fetch_catalogs()
    snapshots = [(int(c["inventory_id"]), fetch_products(int(c["inventory_id"]))) for c in catalogs]
    timestamp = now()
    with database() as db:
        upsert_catalogs(db, catalogs, timestamp)
        db.execute("UPDATE base_products SET active=0")
        count = 0
        for inventory_id, products in snapshots:
            for product_id, product in products.items():
                parent_id = int(product.get("parent_id", 0))
                text_fields = product.get("text_fields") or {}
                name = text_fields.get("name", product.get("name", ""))
                db.execute("""INSERT INTO base_products(inventory_id,product_id,parent_id,sku,name,raw_json,updated_at)
                    VALUES(?,?,?,?,?,?,?) ON CONFLICT(inventory_id,product_id) DO UPDATE SET
                    parent_id=excluded.parent_id,sku=excluded.sku,name=excluded.name,
                    raw_json=excluded.raw_json,updated_at=excluded.updated_at,active=1""",
                           (inventory_id, int(product_id), parent_id, str(product.get("sku", "")),
                            str(name), encode(product), timestamp))
                count += 1
        progress(db, job_id, count)


def stock_number(values, warehouse):
    # A missing field is unknown, never silently converted to zero.
    return str(amount(values[warehouse])) if warehouse in values else None


def sync_inventory(job_id):
    from app.main import database
    catalogs = fetch_catalogs()
    warehouses = sequence(call("getInventoryWarehouses", {}), "warehouses")
    rows = []
    timestamp = now()
    for catalog in catalogs:
        inventory_id = int(catalog["inventory_id"])
        products = fetch_products(inventory_id)
        for product_id, product in products.items():
            values = {name: mapping(product, name) if name in product else {}
                      for name in ("stock", "reservations", "incoming")}
            for warehouse in set().union(*(set(x) for x in values.values())):
                rows.append((inventory_id, int(product_id), warehouse,
                             *(stock_number(values[name], warehouse) for name in values),
                             encode({name: values[name].get(warehouse) for name in values}), timestamp))
    # Publish only a complete snapshot; failure above leaves the previous one intact.
    with database() as db:
        upsert_catalogs(db, catalogs, timestamp)
        db.execute("UPDATE base_warehouses SET active=0")
        for warehouse in warehouses:
            kind = str(warehouse["warehouse_type"])
            key = f"{kind}_{warehouse['warehouse_id']}"
            db.execute("""INSERT INTO base_warehouses(warehouse_key,warehouse_type,internal_id,name,raw_json,updated_at)
                VALUES(?,?,?,?,?,?) ON CONFLICT(warehouse_key) DO UPDATE SET
                warehouse_type=excluded.warehouse_type,internal_id=excluded.internal_id,name=excluded.name,
                raw_json=excluded.raw_json,updated_at=excluded.updated_at,active=1""",
                       (key, kind, warehouse.get("internal_warehouse_id"), warehouse.get("name", ""),
                        encode(warehouse), timestamp))
        db.execute("DELETE FROM base_stock")
        db.executemany("INSERT INTO base_stock VALUES(?,?,?,?,?,?,?,?)", rows)
        progress(db, job_id, len(rows))


def save_order_page(db, orders, timestamp):
    for order in orders:
        order_id = int(order["order_id"])
        if order_id <= 0:
            raise ValueError("invalid order id")
        products = order.get("products")
        if products is None or products == {}:
            # Incomplete orders can omit products entirely. Preserve the exact
            # source JSON while projecting an empty item list until a refresh.
            products = []
        if not isinstance(products, list):
            raise ValueError("invalid products")
        db.execute("""INSERT INTO base_orders VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(order_id)
            DO UPDATE SET external_order_id=excluded.external_order_id,platform=excluded.platform,
            shop_id=excluded.shop_id,created_at=excluded.created_at,confirmed_at=excluded.confirmed_at,
            confirmed=excluded.confirmed,status_id=excluded.status_id,currency=excluded.currency,
            raw_json=excluded.raw_json,updated_at=excluded.updated_at""",
                   (order_id, str(order.get("external_order_id", "")), str(order.get("order_source", "")),
                    str(order.get("order_source_id", "")), order.get("date_add"), order.get("date_confirmed"),
                    int(bool(order.get("confirmed", False))), order.get("order_status_id"),
                    order.get("currency"), encode(order), timestamp))
        db.execute("DELETE FROM base_order_items WHERE order_id=?", (order_id,))
        for position, product in enumerate(products):
            db.execute("INSERT INTO base_order_items VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (order_id, position, product.get("order_product_id"), product.get("storage"),
                        product.get("storage_id"), product.get("product_id"), product.get("variant_id"),
                        product.get("sku"), str(amount(product["quantity"])), encode(product)))


def sync_orders(job_id):
    from app.main import database
    cursor = 0
    # Full reconciliation intentionally revisits old orders, including status/extra-field changes.
    # IDs avoid the confirmation-time same-second pagination gap.
    while True:
        orders = sequence(call("getOrders", {
            "id_from": cursor, "get_unconfirmed_orders": True,
            "include_custom_extra_fields": True, "include_commissions": True,
            "include_connect_data": True, "include_discounts_data": True}), "orders")
        if not orders:
            break
        ids = [int(order["order_id"]) for order in orders]
        if len(ids) > 100 or ids != sorted(set(ids)) or min(ids) < cursor:
            raise ValueError("order pagination violated id ordering")
        next_cursor = max(ids) + 1
        with database() as db:
            save_order_page(db, orders, now())
            progress(db, job_id, len(orders), next_cursor)
        cursor = next_cursor
        # Continue to an empty page rather than trusting a short page.


SYNC_FUNCTIONS = {"shops": sync_shops, "products": sync_products,
                  "inventory": sync_inventory, "orders": sync_orders}


def run_sync(domain):
    with sync_job(domain) as job_id:
        SYNC_FUNCTIONS[domain](job_id)
    return job_id


@router.post("/sync/{domain}")
def sync(domain: str):
    if domain not in DOMAINS:
        raise HTTPException(404, "不支持的数据域。")
    job_id = run_sync(domain)
    return {"job_id": job_id, "status": "success"}


@router.get("/status")
def status():
    import os
    from app.main import database
    tables = {"products": "base_products", "inventory": "base_stock",
              "orders": "base_orders", "shops": "base_shops"}
    with database() as db:
        counts = {name: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                  for name, table in tables.items()}
        jobs = [dict(row) for row in db.execute("SELECT * FROM base_sync_jobs ORDER BY started_at DESC LIMIT 20")]
    return {"configured": bool(os.environ.get("BASELINKER_API_TOKEN")), "counts": counts, "jobs": jobs}


@router.get("/products")
def products(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    from app.main import database
    with database() as db:
        return [dict(row) for row in db.execute("""SELECT p.inventory_id,p.product_id,p.parent_id,
            p.sku AS child_sku,CASE WHEN p.parent_id=0 THEN p.sku ELSE parent.sku END AS parent_sku,
            p.name,p.active,p.updated_at FROM base_products p LEFT JOIN base_products parent
            ON parent.inventory_id=p.inventory_id AND parent.product_id=p.parent_id
            ORDER BY p.inventory_id,p.product_id LIMIT ? OFFSET ?""", (limit, offset))]


@router.get("/inventory")
def inventory(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    from app.main import database
    with database() as db:
        return [dict(row) for row in db.execute("""SELECT s.inventory_id,s.product_id,p.sku,
            s.warehouse_key,w.name AS warehouse_name,w.warehouse_type,s.stock,s.reservations,
            s.incoming,s.fetched_at FROM base_stock s LEFT JOIN base_products p
            ON p.inventory_id=s.inventory_id AND p.product_id=s.product_id
            LEFT JOIN base_warehouses w ON w.warehouse_key=s.warehouse_key
            ORDER BY s.inventory_id,s.product_id,s.warehouse_key LIMIT ? OFFSET ?""", (limit, offset))]


@router.get("/shops")
def shops():
    from app.main import database
    with database() as db:
        return [dict(row) for row in db.execute("SELECT platform,shop_id,name,active,updated_at FROM base_shops ORDER BY platform,shop_id")]


@router.get("/orders")
def orders(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    from app.main import database
    with database() as db:
        return [dict(row) for row in db.execute("""SELECT o.order_id,o.external_order_id,o.platform,o.shop_id,
            o.created_at,o.confirmed_at,o.confirmed,o.status_id,o.currency,o.updated_at,
            a.sales_owner,a.business_group FROM base_orders o LEFT JOIN order_annotations a
            ON a.order_id=o.order_id ORDER BY o.order_id DESC LIMIT ? OFFSET ?""", (limit, offset))]


@router.get("/orders/{order_id}")
def order_detail(order_id: int):
    from app.main import database
    with database() as db:
        order = db.execute("SELECT raw_json FROM base_orders WHERE order_id=?", (order_id,)).fetchone()
        if order is None:
            raise HTTPException(404, "订单不存在。")
        annotation = db.execute("SELECT * FROM order_annotations WHERE order_id=?", (order_id,)).fetchone()
    local = dict(annotation) if annotation else {"sales_owner": None, "business_group": None, "extra_json": "{}"}
    local["extra_fields"] = json.loads(local.pop("extra_json"))
    return {"source_order": json.loads(order["raw_json"]), "annotation": local}


class Annotation(BaseModel):
    sales_owner: str | None = Field(default=None, max_length=200)
    business_group: str | None = Field(default=None, max_length=200)
    extra_fields: dict = Field(default_factory=dict)


@router.patch("/orders/{order_id}/annotation")
def annotate(order_id: int, body: Annotation):
    from app.main import database
    with database() as db:
        if db.execute("SELECT 1 FROM base_orders WHERE order_id=?", (order_id,)).fetchone() is None:
            raise HTTPException(404, "订单不存在。")
        existing = db.execute("SELECT * FROM order_annotations WHERE order_id=?", (order_id,)).fetchone()
        owner = existing["sales_owner"] if existing else None
        group = existing["business_group"] if existing else None
        extra = json.loads(existing["extra_json"]) if existing else {}
        if "sales_owner" in body.model_fields_set:
            owner = body.sales_owner
        if "business_group" in body.model_fields_set:
            group = body.business_group
        if "extra_fields" in body.model_fields_set:
            extra = body.extra_fields
        try:
            raw = encode(extra)
        except (TypeError, ValueError) as error:
            raise HTTPException(422, "附加字段必须是有效 JSON。") from error
        if len(raw.encode()) > 65536:
            raise HTTPException(422, "附加字段不能超过 64 KiB。")
        db.execute("""INSERT INTO order_annotations VALUES(?,?,?,?,?) ON CONFLICT(order_id)
            DO UPDATE SET sales_owner=excluded.sales_owner,business_group=excluded.business_group,
            extra_json=excluded.extra_json,updated_at=excluded.updated_at""", (order_id, owner, group, raw, now()))
    return {"order_id": order_id, "sales_owner": owner, "business_group": group, "extra_fields": extra}
