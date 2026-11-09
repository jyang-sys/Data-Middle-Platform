"""Source inventory, group shares and channel allocations are separate ledgers."""
import hashlib
import json
import uuid
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.access import privileged, visible_stock
from app.catalog import ancestry, read_catalog, stock_node
from app.ingestion import encode, now
from app.management import record_history, reference

router = APIRouter(prefix='/api/allocations', tags=['库存份额与渠道分配'])
SCHEMA = '''
CREATE TABLE IF NOT EXISTS stock_allocations (
    inventory_id INTEGER NOT NULL,product_id INTEGER NOT NULL,warehouse_key TEXT NOT NULL,
    mode TEXT NOT NULL,stock_kind TEXT NOT NULL,document_json TEXT NOT NULL,
    version INTEGER NOT NULL,updated_at TEXT NOT NULL,
    PRIMARY KEY(inventory_id,product_id,warehouse_key)
);
'''


def initialize_storage():
    from app.main import database
    with database() as db:
        db.executescript(SCHEMA)


def amount(value):
    try:
        value = Decimal(str(value))
        return value if value.is_finite() else None
    except InvalidOperation:
        return None


def source_token(row):
    return hashlib.sha256(encode([row['stock'], row['reservations'], row['incoming'], row['fetched_at']]).encode()).hexdigest()


class Channel(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: str = Field(pattern='^(dedicated|shared)$')
    platform: str | None = Field(default=None, min_length=1, max_length=100)
    shop_id: str | None = Field(default=None, min_length=1, max_length=100)
    shared_platforms: list[str] = Field(default_factory=list, max_length=30)
    quantity: int = Field(ge=0, strict=True)

    @model_validator(mode='after')
    def validate_channel(self):
        if self.kind == 'dedicated' and (not self.platform or self.shared_platforms):
            raise ValueError('独占分配需指定平台，不填写共享平台')
        if self.kind == 'shared' and (self.platform or self.shop_id or len(set(self.shared_platforms)) < 2 or len(set(self.shared_platforms)) != len(self.shared_platforms)):
            raise ValueError('共享池需至少两个不同平台，不指定独占平台或店铺')
        return self


class Share(BaseModel):
    model_config = ConfigDict(extra='forbid')
    group_id: int = Field(gt=0)
    quantity: int = Field(default=0, ge=0, strict=True)
    channels: list[Channel] = Field(default_factory=list, max_length=100)


class AllocationInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    inventory_id: int = Field(gt=0)
    product_id: int = Field(gt=0)
    warehouse_key: str = Field(min_length=1, max_length=100)
    mode: str = Field(pattern='^(unassigned|encoded|split)$')
    stock_kind: str = Field(pattern='^(unknown|physical|virtual|return_pending|scrap|in_transit)$')
    shares: list[Share] = Field(default_factory=list, max_length=100)
    expected_version: int = Field(ge=0)
    expected_source_token: str = Field(min_length=64, max_length=64)
    actor: str = Field(min_length=1, max_length=100)
    reason: str = Field(default='', max_length=500)

    @model_validator(mode='after')
    def validate_mode(self):
        self.actor = self.actor.strip()
        if not self.actor or len({s.group_id for s in self.shares}) != len(self.shares):
            raise ValueError('操作人不能为空，同一来源记录的小组不能重复')
        if self.mode == 'unassigned' and self.shares:
            raise ValueError('未分配模式不填写份额')
        if self.mode == 'encoded' and len(self.shares) != 1:
            raise ValueError('编码归组模式只能指定一个小组，数量随来源库存变化')
        return self


@router.put('/source')
def save_allocation(body: AllocationInput):
    from app.main import database
    identity = (body.inventory_id, body.product_id, body.warehouse_key)
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        source = db.execute('SELECT * FROM base_stock WHERE inventory_id=? AND product_id=? AND warehouse_key=?', identity).fetchone()
        if source is None:
            raise HTTPException(404, '来源库存不存在。')
        old = db.execute('SELECT * FROM stock_allocations WHERE inventory_id=? AND product_id=? AND warehouse_key=?', identity).fetchone()
        if (old['version'] if old else 0) != body.expected_version or source_token(source) != body.expected_source_token:
            raise HTTPException(409, '来源库存或份额已变化，请重新查询。')
        quantity = amount(source['stock'])
        assigned = sum(s.quantity for s in body.shares) if body.mode == 'split' else quantity if body.mode == 'encoded' else Decimal(0)
        if body.mode != 'unassigned' and (quantity is None or quantity < 0 or quantity != quantity.to_integral_value()):
            raise HTTPException(422, '来源数量未知、为负或不是整数，不能建立新的件数分配。')
        if body.mode == 'split' and assigned > quantity:
            raise HTTPException(422, '小组份额合计不能超过来源库存。')
        if body.stock_kind in ('virtual', 'return_pending', 'scrap', 'in_transit') and any(s.channels for s in body.shares):
            raise HTTPException(422, '当前库存类型不能分配为渠道库存。')
        documents = []
        for share in body.shares:
            group = reference(db, 'business_groups', share.group_id)
            limit = quantity if body.mode == 'encoded' else share.quantity
            if sum(c.quantity for c in share.channels) > limit:
                raise HTTPException(422, '渠道分配不能超过该小组份额。')
            scopes = set()
            for channel in share.channels:
                platforms = channel.shared_platforms if channel.kind == 'shared' else [channel.platform]
                for platform in platforms:
                    if not db.execute('SELECT 1 FROM base_platforms WHERE platform=? UNION SELECT 1 FROM base_shops WHERE platform=?', (platform, platform)).fetchone():
                        raise HTTPException(422, '分配平台不存在。')
                if channel.shop_id and not db.execute('SELECT 1 FROM base_shops WHERE platform=? AND shop_id=? AND active=1', (channel.platform, channel.shop_id)).fetchone():
                    raise HTTPException(422, '分配店铺不存在或停用。')
                scope = encode([channel.kind, channel.platform, channel.shop_id, sorted(channel.shared_platforms)])
                if scope in scopes:
                    raise HTTPException(422, '同组渠道范围重复，请合并数量。')
                scopes.add(scope)
            entry = share.model_dump()
            entry['business_group'] = group['name']
            if body.mode == 'encoded':
                entry['quantity'] = None
            documents.append(entry)
        document = encode(documents)
        if old and old['mode'] == body.mode and old['stock_kind'] == body.stock_kind and old['document_json'] == document:
            return {'version': old['version']}
        db.execute('''INSERT INTO stock_allocations VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(inventory_id,product_id,warehouse_key)
            DO UPDATE SET mode=excluded.mode,stock_kind=excluded.stock_kind,document_json=excluded.document_json,
            version=excluded.version,updated_at=excluded.updated_at''', (*identity, body.mode, body.stock_kind, document, body.expected_version+1, now()))
        saved = dict(db.execute('SELECT * FROM stock_allocations WHERE inventory_id=? AND product_id=? AND warehouse_key=?', identity).fetchone())
        record_history(db, 'stock_allocation', encode(identity), dict(old) if old else {}, saved, body.actor, body.reason, uuid.uuid4().hex)
        return {'version': saved['version']}


def source_rows(db, inventory_id=None):
    query = '''SELECT s.inventory_id,s.product_id,s.warehouse_key,s.stock,s.reservations,s.incoming,s.fetched_at,
        p.sku,p.name,p.parent_id,parent.sku AS parent_sku,COALESCE(w.name,s.warehouse_key) AS warehouse_name,
        w.warehouse_type,w.raw_json AS warehouse_json,c.name AS catalog_name,a.mode,a.stock_kind,a.document_json,
        COALESCE(a.version,0) AS allocation_version FROM base_stock s
        LEFT JOIN base_products p ON p.inventory_id=s.inventory_id AND p.product_id=s.product_id
        LEFT JOIN base_products parent ON parent.inventory_id=p.inventory_id AND parent.product_id=p.parent_id
        LEFT JOIN base_warehouses w ON w.warehouse_key=s.warehouse_key
        LEFT JOIN base_catalogs c ON c.inventory_id=s.inventory_id
        LEFT JOIN stock_allocations a ON a.inventory_id=s.inventory_id AND a.product_id=s.product_id AND a.warehouse_key=s.warehouse_key'''
    return [dict(r) for r in db.execute(query + (' WHERE s.inventory_id=?' if inventory_id else '') + ' ORDER BY s.inventory_id,s.product_id,s.warehouse_key', (inventory_id,) if inventory_id else ())]


def expanded_source(row, nodes, mapping):
    row['source_token'] = source_token(row)
    row['mode'] = row['mode'] or 'unassigned'
    row['stock_kind'] = row['stock_kind'] or 'unknown'
    row['country'] = json.loads(row.pop('warehouse_json') or '{}').get('country') or None
    row['company_node_id'] = stock_node(mapping, row)
    row['company_path'] = ancestry(nodes, row['company_node_id'])
    row['company_sku'] = nodes[row['company_node_id']]['code'] if row['company_node_id'] else None
    row['company_name'] = nodes[row['company_node_id']]['name'] if row['company_node_id'] else None
    quantity = amount(row['stock'])
    shares = json.loads(row.pop('document_json') or '[]')
    for share in shares:
        if row['mode'] == 'encoded':
            share['quantity'] = str(quantity) if quantity is not None else None
    assigned = sum((amount(s['quantity']) or Decimal(0) for s in shares), Decimal(0))
    difference = quantity-assigned if quantity is not None else None
    row['difference'] = str(difference) if difference is not None else None
    row['allocated_quantity'] = str(assigned)
    row['unassigned_quantity'] = str(max(Decimal(0), difference)) if difference is not None else None
    row['allocation_conflict'] = difference is not None and difference < 0
    row['channel_conflict'] = any(amount(s['quantity']) is None or sum(c['quantity'] for c in s['channels']) > amount(s['quantity']) for s in shares)
    if difference is None or difference > 0 or not shares:
        shares.append({'group_id': None, 'business_group': '未分配', 'quantity': row['unassigned_quantity'], 'channels': []})
    row['shares'] = shares
    return row


@router.get('/view')
def view(inventory_id: int | None = Query(default=None, ge=1), node_id: int | None = Query(default=None, ge=1),
         warehouse_key: str | None = None, group_id: str | None = Query(default=None, pattern='^(unassigned|[1-9][0-9]*)$'),
         platform: str | None = None, search: str = ''):
    from app.main import database
    with database() as db:
        db.execute('BEGIN')
        nodes, mapping = read_catalog(db)
        if node_id is not None and node_id not in nodes:
            raise HTTPException(422, '公司产品节点不存在。')
        rows = [expanded_source(r, nodes, mapping) for r in source_rows(db, inventory_id)]
    result = []
    for row in rows:
        if (warehouse_key and row['warehouse_key'] != warehouse_key) or (node_id and node_id not in row['company_path']):
            continue
        if search and search.casefold() not in ' '.join(str(row.get(k) or '') for k in ('sku','parent_sku','name','company_sku','company_name')).casefold():
            continue
        shares = [s for s in row['shares'] if visible_stock(s['group_id']) and (group_id is None or
                  (s['group_id'] is None if group_id == 'unassigned' else s['group_id'] == int(group_id)))]
        if (not privileged() or group_id is not None) and not shares:
            continue
        selected = None
        if platform:
            selected = sum(c['quantity'] for s in shares for c in s['channels'] if c['kind'] == 'dedicated' and c['platform'] == platform)
            if not selected:
                continue
        elif group_id is not None or not privileged():
            values = [amount(s['quantity']) for s in shares]
            selected = sum(values, Decimal(0)) if all(v is not None for v in values) else None
        quantity = str(selected) if selected is not None else row['stock'] if privileged() and group_id is None and not platform else None
        row['shares'] = shares
        if not privileged():
            for key in ('allocated_quantity','unassigned_quantity','difference','source_token'):
                row.pop(key, None)
            row['reservations'] = row['incoming'] = None
            row['allocation_conflict'] = row['channel_conflict'] = False
        else:
            row['source_stock'] = row['stock']
        row['stock'] = quantity
        result.append(row)
    groups = {}
    for row in result:
        identity = ('company', row['company_node_id']) if row['company_node_id'] else ('source', row['inventory_id'], row['product_id'])
        key = encode(identity)
        target = groups.setdefault(key, {'key': key, 'company_node_id': row['company_node_id'], 'name': row['company_name'] or row['name'] or row['sku'],
                                        'sku': row['company_sku'] or row['sku'], 'warehouses': {}, 'physical_total': '0', 'unknown_physical_rows': 0})
        warehouse = target['warehouses'].setdefault(row['warehouse_key'], {'name': row['warehouse_name'], 'physical': '0', 'unknown_rows': 0})
        q = amount(row['stock'])
        if row['stock_kind'] == 'physical' and q is not None:
            target['physical_total'] = str(Decimal(target['physical_total'])+q)
            warehouse['physical'] = str(Decimal(warehouse['physical'])+q)
        elif row['stock_kind'] in ('unknown', 'physical'):
            target['unknown_physical_rows'] += 1
            warehouse['unknown_rows'] += 1
    return {'rows': result, 'products': list(groups.values()), 'row_count': len(result),
            'privileged': privileged(), 'platform_basis': '独占渠道份额；共享池单独列出，不复制计入各平台'}
