"""Company product tree and explicit, scoped links to source identifiers."""
import sqlite3
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.ingestion import encode, now
from app.management import record_history

router = APIRouter(prefix='/api/catalog', tags=['公司产品层级'])
SCHEMA = '''
CREATE TABLE IF NOT EXISTS company_products (
    id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
    parent_id INTEGER REFERENCES company_products(id), kind TEXT NOT NULL,
    attributes_json TEXT NOT NULL, active INTEGER NOT NULL, version INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS product_source_links (
    domain TEXT NOT NULL, source_key TEXT NOT NULL, node_id INTEGER NOT NULL REFERENCES company_products(id),
    version INTEGER NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(domain,source_key)
);
'''


def initialize_storage():
    from app.main import database
    with database() as db:
        db.executescript(SCHEMA)


class ProductInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: int | None = Field(default=None, gt=0)
    code: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    parent_id: int | None = Field(default=None, gt=0)
    kind: str = Field(pattern='^(group|sku)$')
    attributes: dict[str, str] = Field(default_factory=dict)
    active: bool = True
    expected_version: int = Field(ge=0)
    actor: str = Field(min_length=1, max_length=100)
    reason: str = Field(default='', max_length=500)

    @model_validator(mode='after')
    def trim(self):
        self.code, self.name, self.actor = self.code.strip(), self.name.strip(), self.actor.strip()
        if not all((self.code, self.name, self.actor)) or len(encode(self.attributes).encode()) > 16384:
            raise ValueError('编号、名称和操作人不能为空，属性不能超过 16 KiB')
        return self


@router.get('/products')
def products():
    from app.main import database
    with database() as db:
        rows = [dict(r) for r in db.execute('SELECT * FROM company_products ORDER BY id')]
    import json
    for row in rows:
        row['attributes'] = json.loads(row.pop('attributes_json'))
    return rows


@router.put('/products')
def save_product(body: ProductInput):
    from app.main import database
    try:
        with database() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM company_products WHERE id=?', (body.id,)).fetchone()
            if body.id and old is None:
                raise HTTPException(404, '产品不存在。')
            if (old['version'] if old else 0) != body.expected_version:
                raise HTTPException(409, '产品已修改，请重新读取。')
            parent = body.parent_id
            visited = {body.id} if body.id else set()
            while parent is not None:
                if parent in visited:
                    raise HTTPException(422, '产品层级不能形成循环。')
                visited.add(parent)
                row = db.execute('SELECT * FROM company_products WHERE id=?', (parent,)).fetchone()
                if row is None or row['kind'] != 'group' or not row['active']:
                    raise HTTPException(422, '上级必须是启用的汇总节点。')
                parent = row['parent_id']
            if old and body.kind != old['kind']:
                if db.execute('SELECT 1 FROM company_products WHERE parent_id=?', (body.id,)).fetchone() or db.execute('SELECT 1 FROM product_source_links WHERE node_id=?', (body.id,)).fetchone():
                    raise HTTPException(409, '存在下级或来源关联，不能变更节点类型。')
            if old and not body.active and db.execute('SELECT 1 FROM company_products WHERE parent_id=? AND active=1', (body.id,)).fetchone():
                raise HTTPException(409, '请先处理启用的下级产品。')
            values = dict(code=body.code, name=body.name, parent_id=body.parent_id, kind=body.kind,
                          attributes_json=encode(body.attributes), active=int(body.active))
            if old and all(old[k] == v for k, v in values.items()):
                return {'id': old['id'], 'version': old['version']}
            values.update(version=body.expected_version + 1, updated_at=now())
            if old:
                db.execute('UPDATE company_products SET ' + ','.join(k+'=?' for k in values) + ' WHERE id=?', (*values.values(), body.id))
                identity = body.id
            else:
                identity = db.execute('INSERT INTO company_products (' + ','.join(values) + ') VALUES (' + ','.join('?' for _ in values) + ')', tuple(values.values())).lastrowid
            saved = dict(db.execute('SELECT * FROM company_products WHERE id=?', (identity,)).fetchone())
            record_history(db, 'product', str(identity), dict(old) if old else {}, saved, body.actor, body.reason, uuid.uuid4().hex)
            return saved
    except sqlite3.IntegrityError as error:
        raise HTTPException(409, '公司产品编号已存在。') from error


class LinkInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    domain: str = Field(pattern='^(catalog|sales|stock)$')
    inventory_id: int | None = Field(default=None, gt=0)
    product_id: int | None = Field(default=None, gt=0)
    warehouse_key: str = Field(default='', max_length=100)
    platform: str = Field(default='', max_length=100)
    shop_id: str = Field(default='', max_length=100)
    source_sku: str = Field(default='', max_length=200)
    node_id: int = Field(gt=0)
    expected_version: int = Field(ge=0)
    actor: str = Field(min_length=1, max_length=100)
    reason: str = Field(default='', max_length=500)

    @model_validator(mode='after')
    def validate_key(self):
        self.actor = self.actor.strip()
        if not self.actor:
            raise ValueError('操作人不能为空')
        if self.domain == 'sales':
            if not self.platform or not self.source_sku or self.inventory_id or self.product_id or self.warehouse_key:
                raise ValueError('销售编码需指定平台、原 SKU，可选店铺，不填写仓库或来源产品 ID')
        elif not self.inventory_id or not self.product_id or self.platform or self.shop_id or self.source_sku or (self.domain == 'stock' and not self.warehouse_key) or (self.domain == 'catalog' and self.warehouse_key):
            raise ValueError('来源产品关联需指定目录、产品 ID；仓库关联还需仓库标识')
        return self

    def key(self):
        return encode([self.platform, self.shop_id, self.source_sku] if self.domain == 'sales' else
                      [self.inventory_id, self.warehouse_key, self.product_id] if self.domain == 'stock' else
                      [self.inventory_id, self.product_id])


@router.get('/links')
def links():
    from app.main import database
    with database() as db:
        return [dict(r) for r in db.execute('SELECT * FROM product_source_links ORDER BY domain,source_key')]


@router.put('/links')
def save_link(body: LinkInput):
    from app.main import database
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        node = db.execute('SELECT * FROM company_products WHERE id=?', (body.node_id,)).fetchone()
        if node is None or node['kind'] != 'sku' or not node['active']:
            raise HTTPException(422, '只能关联启用的具体规格 SKU。')
        if body.domain == 'catalog' and not db.execute('SELECT 1 FROM base_products WHERE inventory_id=? AND product_id=?', (body.inventory_id, body.product_id)).fetchone():
            raise HTTPException(422, '来源产品不存在。')
        if body.domain == 'stock' and not db.execute('SELECT 1 FROM base_stock WHERE inventory_id=? AND warehouse_key=? AND product_id=?', (body.inventory_id, body.warehouse_key, body.product_id)).fetchone():
            raise HTTPException(422, '来源库存不存在。')
        if body.domain == 'sales' and not db.execute('SELECT 1 FROM base_shops WHERE platform=? AND (?=\'\' OR shop_id=?)', (body.platform, body.shop_id, body.shop_id)).fetchone():
            raise HTTPException(422, '来源平台或店铺不存在。')
        old = db.execute('SELECT * FROM product_source_links WHERE domain=? AND source_key=?', (body.domain, body.key())).fetchone()
        if (old['version'] if old else 0) != body.expected_version:
            raise HTTPException(409, '映射已修改，请重新读取。')
        if old and old['node_id'] == body.node_id:
            return dict(old)
        db.execute('INSERT INTO product_source_links VALUES(?,?,?,?,?) ON CONFLICT(domain,source_key) DO UPDATE SET node_id=excluded.node_id,version=excluded.version,updated_at=excluded.updated_at', (body.domain, body.key(), body.node_id, body.expected_version+1, now()))
        saved = dict(db.execute('SELECT * FROM product_source_links WHERE domain=? AND source_key=?', (body.domain, body.key())).fetchone())
        record_history(db, 'product_link', encode([body.domain, body.key()]), dict(old) if old else {}, saved, body.actor, body.reason, uuid.uuid4().hex)
        return saved


def read_catalog(db):
    nodes = {r['id']: dict(r) for r in db.execute('SELECT * FROM company_products')}
    mapping = {(r['domain'], r['source_key']): r['node_id'] for r in db.execute('SELECT * FROM product_source_links')}
    return nodes, mapping


def ancestry(nodes, identity):
    result, seen = [], set()
    while identity in nodes and identity not in seen:
        seen.add(identity)
        result.insert(0, identity)
        identity = nodes[identity]['parent_id']
    return result


def sales_node(mapping, order, item, native):
    for key in [('sales', encode([order['platform'], order['shop_id'], item['sku']])),
                ('sales', encode([order['platform'], '', item['sku']])),
                ('catalog', encode([native['inventory_id'], native['product_id']])) if native else ('', '')]:
        if key in mapping:
            return mapping[key]
    return None


def stock_node(mapping, row):
    return mapping.get(('stock', encode([row['inventory_id'], row['warehouse_key'], row['product_id']]))) or mapping.get(('catalog', encode([row['inventory_id'], row['product_id']])))
