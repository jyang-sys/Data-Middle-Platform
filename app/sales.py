"""BaseLinker read-only order import and sales summaries. No customer PII is retained."""
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/sales")
SYNC_LOCK = threading.Lock()
API_URL = "https://api.baselinker.com/connector.php"


def api_request(method, parameters):
    token = os.environ.get("BASELINKER_API_TOKEN")
    if not token:
        raise HTTPException(503, "尚未配置 BaseLinker API 令牌，请在环境设置中添加 BASELINKER_API_TOKEN。")
    from app.ingestion import throttle
    throttle()
    body = urllib.parse.urlencode({"method": method, "parameters": json.dumps(parameters)}).encode()
    request = urllib.request.Request(API_URL, data=body, headers={
        "X-BLToken": token, "Content-Type": "application/x-www-form-urlencoded",
    })
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read(20 * 1024 * 1024 + 1)
        if len(raw) > 20 * 1024 * 1024:
            raise HTTPException(502, "BaseLinker 响应过大，请缩小同步范围。")
        payload = json.loads(raw)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise HTTPException(502, "无法连接 BaseLinker，请检查网络权限和 API 访问设置。") from error
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise HTTPException(502, "BaseLinker 返回了无法解析的响应。") from error
    if not isinstance(payload, dict) or payload.get("status") != "SUCCESS":
        # Do not forward upstream descriptions: they may contain credential values.
        if isinstance(payload, dict) and payload.get("error_code") == "ERROR_BAD_TOKEN":
            raise HTTPException(502, "BaseLinker 返回 ERROR_BAD_TOKEN：请在环境设置中更新 BASELINKER_API_TOKEN。")
        raise HTTPException(502, "BaseLinker 拒绝了请求，请检查令牌、权限或接口限额。")
    return payload


def api_call(parameters):
    payload = api_request("getOrders", parameters)
    orders = payload.get("orders")
    if not isinstance(orders, list):
        raise HTTPException(502, "BaseLinker 响应中缺少订单列表。")
    return orders


def get_sources():
    sources = api_request("getOrderSources", {}).get("sources")
    if not isinstance(sources, dict):
        raise HTTPException(502, "BaseLinker 响应中缺少来源列表。")
    result = []
    for platform, entries in sources.items():
        # The official example uses an indexed list for order_return.
        if isinstance(entries, list):
            entries = dict(enumerate(entries))
        if not isinstance(entries, dict) or any(not isinstance(name, str) for name in entries.values()):
            raise HTTPException(502, "BaseLinker 来源列表格式错误。")
        result.extend((str(platform), str(shop), name) for shop, name in entries.items())
    return result


def amount(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError("non-finite amount")
        return result
    except (InvalidOperation, TypeError, ValueError) as error:
        raise HTTPException(502, "订单包含无效金额或数量，同步未保存。") from error


def normalize(order):
    try:
        order_id = int(order["order_id"])
        confirmed_at = int(order["date_confirmed"])
        currency = str(order["currency"]).upper()
        products = order["products"]
        if order_id <= 0 or confirmed_at <= 0 or len(currency) != 3 or not currency.isalpha() or not isinstance(products, list):
            raise ValueError("invalid order")
        # Ensure dashboard date conversion can handle the supplied timestamp.
        datetime.fromtimestamp(confirmed_at, timezone.utc)
        delivery = amount(order.get("delivery_price", 0))
        items = []
        for position, product in enumerate(products):
            quantity = amount(product["quantity"])
            total = amount(product["price_brutto"]) * quantity
            items.append((position, str(product.get("name", "未命名商品")), str(quantity), str(total),
                          str(product.get("sku") or "未提供 SKU")))
        total = delivery + sum((Decimal(item[3]) for item in items), Decimal(0))
        return (order_id, confirmed_at, currency, str(total), str(delivery),
                int(order.get("order_status_id", 0)), str(order.get("order_source") or "未提供平台"),
                str(order.get("order_source_id") if order.get("order_source_id") is not None else "未提供"), items)
    except (KeyError, ValueError, TypeError, OverflowError, OSError) as error:
        raise HTTPException(502, "BaseLinker 订单字段不完整，同步未保存。") from error


def save_orders(source, orders, sources=None):
    from app.main import database
    # Validate the entire batch before committing any changes.
    normalized = [normalize(order) for order in orders]
    with database() as connection:
        if sources is not None:
            connection.execute("DELETE FROM sales_sources WHERE source = ?", (source,))
            connection.executemany("INSERT INTO sales_sources VALUES (?, ?, ?, ?)",
                                   [(source, *entry) for entry in sources])
        for order in normalized:
            order_id, confirmed_at, currency, total, delivery, status_id, platform, shop, items = order
            connection.execute("INSERT OR REPLACE INTO sales_orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                               (source, order_id, confirmed_at, currency, total, delivery, status_id, platform, shop))
            connection.execute("DELETE FROM sales_items WHERE source = ? AND order_id = ?", (source, order_id))
            connection.executemany("INSERT INTO sales_items VALUES (?, ?, ?, ?, ?, ?, ?)",
                                   [(source, order_id, *item) for item in items])
        connection.execute("INSERT OR REPLACE INTO sync_state VALUES (?, ?, ?)",
                           (source, datetime.now(timezone.utc).isoformat(), len(normalized)))
    return len(normalized)


class SyncRequest(BaseModel):
    days: int = Field(default=30, ge=1, le=365)


@router.get("/connection")
def connection_status():
    from app.main import database
    with database() as connection:
        row = connection.execute("SELECT * FROM sync_state WHERE source = 'baselinker'").fetchone()
    return {"configured": bool(os.environ.get("BASELINKER_API_TOKEN")),
            "last_sync": dict(row) if row else None}


@router.post("/sync")
def sync(body: SyncRequest):
    if not SYNC_LOCK.acquire(blocking=False):
        raise HTTPException(409, "已有同步任务正在执行，请稍后再试。")
    try:
        cutoff = int(time.time()) - body.days * 86400
        sources = get_sources()
        cursor_date = cutoff
        all_orders = {}
        # Follow documented confirmation-time pagination, retaining the boundary
        # second and deduplicating IDs to avoid skipping equal-time confirmations.
        for page in range(101):
            time.sleep(0.7)  # Includes getOrderSources in the request-rate budget.
            orders = api_call({"date_confirmed_from": cursor_date,
                               "get_unconfirmed_orders": False})
            if not orders:
                break
            if page == 100:
                raise HTTPException(422, "分页次数超过限制，请缩短同步天数；本次未保存。")
            normalized = [normalize(order) for order in orders]
            dates = [order[1] for order in normalized]
            if len(orders) > 100 or dates != sorted(dates) or dates[0] < cursor_date:
                raise HTTPException(502, "BaseLinker 分页响应不符合确认时间顺序，本次同步未保存。")
            previous_count = len(all_orders)
            for raw, parsed in zip(orders, normalized):
                if parsed[1] >= cutoff:
                    all_orders[parsed[0]] = raw
            if len(all_orders) > 10_000:
                raise HTTPException(422, "订单超过 10,000 条，请缩短同步天数；本次未保存。")
            if len(orders) < 100:
                break
            if len(all_orders) == previous_count:
                raise HTTPException(422, "同一秒的订单分页无法完整读取；为避免漏单本次未保存，需改用订单日志方式同步。")
            cursor_date = dates[-1]
        count = save_orders("baselinker", list(all_orders.values()), sources=sources)
        return {"orders": count, "days": body.days, "message": f"已同步 {count} 条已确认订单。"}
    finally:
        SYNC_LOCK.release()


@router.post("/demo")
def load_demo():
    today = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    orders = []
    for day in range(30):
        for item in range(2 + day % 5):
            orders.append({"order_id": day * 10 + item + 1,
                           "date_confirmed": int((today - timedelta(days=day)).timestamp()),
                           "currency": "PLN", "delivery_price": "15.00", "order_status_id": 1,
                           "order_source": ["allegro", "amazon", "shop"][item % 3],
                           "order_source_id": 100 + item % 3,
                           "products": [{"name": ["无线耳机", "桌面收纳", "便携音箱"][item % 3],
                                         "sku": ["EAR-001", "BOX-002", "SPK-003"][item % 3],
                                         "price_brutto": ["129.90", "49.50", "89.00"][item % 3],
                                         "quantity": 1 + (day + item) % 3}]})
    from app.main import database
    with database() as connection:
        connection.execute("DELETE FROM sales_items WHERE source = 'demo'")
        connection.execute("DELETE FROM sales_orders WHERE source = 'demo'")
    count = save_orders("demo", orders, sources=[("allegro", "100", "Allegro 演示店"),
                                                ("amazon", "101", "Amazon 演示店"),
                                                ("shop", "102", "独立站演示店")])
    return {"orders": count, "message": "演示订单已加载，与真实数据分开存储。"}


@router.get("/dashboard")
def dashboard(source: str = Query(default="baselinker", pattern="^(baselinker|demo)$"),
              days: int = Query(default=30, ge=1, le=365), currency: str | None = None):
    from app.main import database
    cutoff = int(time.time()) - days * 86400
    with database() as connection:
        orders = connection.execute("SELECT * FROM sales_orders WHERE source = ? AND confirmed_at >= ?",
                                    (source, cutoff)).fetchall()
        currencies = sorted({row["currency"] for row in orders})
        selected_currency = currency.upper() if currency else (currencies[0] if currencies else None)
        orders = [row for row in orders if row["currency"] == selected_currency]
        items = connection.execute("""SELECT i.*, o.platform, o.shop FROM sales_items i JOIN sales_orders o
            ON i.source = o.source AND i.order_id = o.order_id
            WHERE o.source = ? AND o.confirmed_at >= ? AND o.currency = ?""",
                                   (source, cutoff, selected_currency)).fetchall()
        state = connection.execute("SELECT * FROM sync_state WHERE source = ?", (source,)).fetchone()
        source_names = {(row["platform"], row["shop"]): row["name"] for row in
                        connection.execute("SELECT * FROM sales_sources WHERE source = ?", (source,))}
    revenue = sum((Decimal(row["total"]) for row in orders), Decimal(0))
    delivery = sum((Decimal(row["delivery"]) for row in orders), Decimal(0))
    daily = {}
    for offset in range(days - 1, -1, -1):
        day = (datetime.now(timezone.utc) - timedelta(days=offset)).date().isoformat()
        daily[day] = {"date": day, "revenue": Decimal(0), "orders": 0}
    for row in orders:
        day = datetime.fromtimestamp(row["confirmed_at"], timezone.utc).date().isoformat()
        if day not in daily:
            daily[day] = {"date": day, "revenue": Decimal(0), "orders": 0}
        daily[day]["revenue"] += Decimal(row["total"])
        daily[day]["orders"] += 1
    products = {}
    platforms = {}
    shops = {}
    for row in orders:
        platform = platforms.setdefault(row["platform"], {"name": row["platform"], "orders": 0, "quantity": Decimal(0), "revenue": Decimal(0)})
        shop = shops.setdefault((row["platform"], row["shop"]), {"platform": row["platform"], "id": row["shop"], "name": source_names.get((row["platform"], row["shop"]), row["shop"]), "orders": 0, "quantity": Decimal(0), "revenue": Decimal(0)})
        for group in (platform, shop):
            group["orders"] += 1
            group["revenue"] += Decimal(row["total"])
    for item in items:
        product = products.setdefault(item["sku"], {"sku": item["sku"], "name": item["name"], "quantity": Decimal(0), "revenue": Decimal(0)})
        product["quantity"] += Decimal(item["quantity"])
        product["revenue"] += Decimal(item["total"])
        platforms[item["platform"]]["quantity"] += Decimal(item["quantity"])
        shops[(item["platform"], item["shop"])]["quantity"] += Decimal(item["quantity"])
    top_products = sorted(products.values(), key=lambda item: item["quantity"], reverse=True)[:20]
    def serial_decimal(items):
        return [{key: float(value) if isinstance(value, Decimal) else value for key, value in item.items()}
                for item in items]
    recent = sorted(orders, key=lambda row: row["confirmed_at"], reverse=True)[:20]
    return {"source": source, "currency": selected_currency, "currencies": currencies,
            "revenue": float(revenue), "orders": len(orders),
            "average_order": float(revenue / len(orders)) if orders else 0,
            "delivery": float(delivery), "units": float(sum((Decimal(row["quantity"]) for row in items), Decimal(0))),
            "daily": serial_decimal(sorted(daily.values(), key=lambda item: item["date"])),
            "products": serial_decimal(top_products),
            "platforms": serial_decimal(sorted(platforms.values(), key=lambda item: item["quantity"], reverse=True)),
            "shops": serial_decimal(sorted(shops.values(), key=lambda item: item["quantity"], reverse=True)),
            "last_sync": dict(state) if state else None,
            "recent_orders": [{"id": row["order_id"],
                               "date": datetime.fromtimestamp(row["confirmed_at"], timezone.utc).isoformat(),
                               "total": float(Decimal(row["total"])), "status_id": row["status_id"]} for row in recent]}
