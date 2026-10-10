"""Source inventory by catalog/warehouse, without inferring physical availability."""
import json
from decimal import Decimal, InvalidOperation
from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix='/api/stock-dashboard', tags=['来源库存看板'])


def quantity(value):
    if value is None:
        return None
    try:
        number = Decimal(value)
        return str(number) if number.is_finite() else None
    except (InvalidOperation, TypeError):
        return None


def total(rows, field):
    values = [quantity(row[field]) for row in rows]
    return {'value': str(sum((Decimal(v) for v in values if v is not None), Decimal(0))),
            'known_rows': sum(v is not None for v in values), 'unknown_rows': sum(v is None for v in values)}


@router.get('/options')
def options():
    from app.main import database
    with database() as db:
        catalogs = [dict(r) for r in db.execute('''SELECT c.inventory_id AS id,c.name,COUNT(s.product_id) AS rows
            FROM base_catalogs c JOIN base_stock s ON s.inventory_id=c.inventory_id GROUP BY c.inventory_id ORDER BY c.inventory_id''')]
        warehouses = [dict(r) for r in db.execute('''SELECT DISTINCT s.inventory_id,s.warehouse_key AS id,
            COALESCE(w.name,s.warehouse_key) AS name,w.warehouse_type,w.raw_json AS warehouse_json FROM base_stock s
            LEFT JOIN base_warehouses w ON w.warehouse_key=s.warehouse_key ORDER BY s.inventory_id,s.warehouse_key''')]
        common = db.execute('''SELECT CAST(storage_id AS INTEGER) id,COUNT(*) n FROM base_order_items
            WHERE storage='db' GROUP BY storage_id ORDER BY n DESC LIMIT 1''').fetchone()
    for warehouse in warehouses:
        warehouse['country'] = json.loads(warehouse.pop('warehouse_json') or '{}').get('country') or None
        warehouse['provider'] = warehouse['account'] = None
    default = common['id'] if common and any(c['id'] == common['id'] for c in catalogs) else catalogs[0]['id'] if catalogs else None
    return {'catalogs': catalogs, 'warehouses': warehouses, 'default_catalog': default}


@router.get('/summary')
def summary(inventory_id: int = Query(ge=1), warehouse_key: str | None = None, search: str = '',
            nonzero_only: bool = False, product_id: int | None = Query(default=None, ge=1), offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
    from app.main import database
    with database() as db:
        catalog = db.execute('SELECT inventory_id,name FROM base_catalogs WHERE inventory_id=?', (inventory_id,)).fetchone()
        if catalog is None:
            raise HTTPException(404, '库存目录不存在。')
        rows = [dict(r) for r in db.execute('''SELECT s.inventory_id,s.product_id,s.warehouse_key,s.stock,
            s.reservations,s.incoming,s.fetched_at,p.sku,p.name,p.parent_id,parent.sku AS parent_sku,
            COALESCE(w.name,s.warehouse_key) AS warehouse_name,w.warehouse_type
            FROM base_stock s LEFT JOIN base_products p ON p.inventory_id=s.inventory_id AND p.product_id=s.product_id
            LEFT JOIN base_products parent ON parent.inventory_id=p.inventory_id AND parent.product_id=p.parent_id
            LEFT JOIN base_warehouses w ON w.warehouse_key=s.warehouse_key
            WHERE s.inventory_id=? ORDER BY s.warehouse_key,s.product_id''', (inventory_id,))]
    rows = [r for r in rows if (warehouse_key is None or r['warehouse_key'] == warehouse_key)
            and (product_id is None or r['product_id'] == product_id)
            and (not search or search.lower() in ((r['sku'] or '') + ' ' + (r['name'] or '') + ' ' + (r['parent_sku'] or '')).lower())
            and (not nonzero_only or quantity(r['stock']) is None or Decimal(quantity(r['stock'])) != 0)]
    groups = {}
    for row in rows:
        groups.setdefault(row['warehouse_key'], []).append(row)
    distributions = [{'warehouse_key': key, 'warehouse_name': items[0]['warehouse_name'], 'warehouse_type': items[0]['warehouse_type'],
                      'row_count': len(items), 'stock': total(items, 'stock'), 'reservations': total(items, 'reservations'),
                      'incoming': total(items, 'incoming'), 'updated_at': min(r['fetched_at'] for r in items),
                      'negative_rows': sum(quantity(r['stock']) is not None and Decimal(quantity(r['stock'])) < 0 for r in items)}
                     for key, items in groups.items()]
    # Only one selected warehouse may have a headline quantity; never combine all warehouses/catalogs.
    selected = distributions[0] if warehouse_key and distributions else None
    return {'catalog': dict(catalog), 'warehouses': distributions, 'total_rows': len(rows),
            'product_count': len({r['product_id'] for r in rows}), 'rows': rows[offset:offset + limit],
            'selected_warehouse': selected, 'physical_stock': None, 'in_transit': None, 'available_stock': None,
            'platform_saleable': None, 'ownership_known': False,
            'warnings': ['Base 库存按目录和仓库分别展示，不跨目录、跨仓合计。',
                         '来源 stock 尚未认定为海外仓实物；reservations 为来源预占，incoming 为供应商待入库，不代表运输在途。',
                         '实物在库、运输在途、可用库存、平台可售、退货状态和库存所属小组尚待字段核对。',
                         '负库存保留来源值并提示，不自动截断为零；各仓更新时间显示本次筛选明细中最早的获取时间。']}


@router.get('/dataset')
def dataset(inventory_id: int = Query(ge=1)):
    """Complete selected-catalog snapshot for client filtering and inventory matrix."""
    from app.main import database
    with database() as db:
        db.execute('BEGIN')
        catalog = db.execute('SELECT inventory_id,name FROM base_catalogs WHERE inventory_id=?', (inventory_id,)).fetchone()
        if catalog is None:
            raise HTTPException(404, '库存目录不存在。')
        rows = [dict(r) for r in db.execute("""SELECT s.inventory_id,s.product_id,s.warehouse_key,s.stock,
            s.reservations,s.incoming,s.fetched_at,p.sku,p.name,p.parent_id,parent.sku AS parent_sku,
            COALESCE(w.name,s.warehouse_key) AS warehouse_name,w.warehouse_type,w.raw_json AS warehouse_json
            FROM base_stock s LEFT JOIN base_products p ON p.inventory_id=s.inventory_id AND p.product_id=s.product_id
            LEFT JOIN base_products parent ON parent.inventory_id=p.inventory_id AND parent.product_id=p.parent_id
            LEFT JOIN base_warehouses w ON w.warehouse_key=s.warehouse_key
            WHERE s.inventory_id=? ORDER BY s.product_id,s.warehouse_key""", (inventory_id,))]
        job = db.execute("SELECT status,finished_at FROM base_sync_jobs WHERE domain='inventory' ORDER BY started_at DESC LIMIT 1").fetchone()
    for row in rows:
        row['country'] = json.loads(row.pop('warehouse_json') or '{}').get('country') or None
        row['provider'] = row['account'] = row['external_sku'] = None
        for field in ('stock', 'reservations', 'incoming'):
            row[field] = quantity(row[field])
    return {'catalog': dict(catalog), 'rows': rows, 'total_rows': len(rows),
            'physical_stock': None, 'available_stock': None, 'in_transit': None, 'platform_saleable': None,
            'last_sync': dict(job) if job else None}
