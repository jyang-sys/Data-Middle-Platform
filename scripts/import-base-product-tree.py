"""Initialize company references from explicit Base IDs and parent relations only."""
import argparse
import json
import os
import sys
import uuid
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))


def import_tree():
    from app import main
    from app.catalog import read_catalog
    from app.ingestion import encode,now
    from app.management import record_history
    with main.database() as db:
        db.execute('BEGIN IMMEDIATE')
        rows=[dict(r) for r in db.execute('SELECT * FROM base_products WHERE active=1 ORDER BY inventory_id,product_id')]
        parents={(r['inventory_id'],r['parent_id']) for r in rows if r['parent_id']}
        by_key={(r['inventory_id'],r['product_id']):r for r in rows}
        for row in rows:
            current=row;seen=set()
            while current:
                key=(current['inventory_id'],current['product_id'])
                if key in seen:raise ValueError('Base 来源父子关系存在循环，未初始化产品树。')
                seen.add(key);current=by_key.get((current['inventory_id'],current['parent_id'])) if current['parent_id'] else None
        nodes,links=read_catalog(db)
        known={}
        for node in nodes.values():
            attrs=json.loads(node['attributes_json'])
            if 'base_product_key' in attrs:known[tuple(attrs['base_product_key'].split(':'))]=node['id']
        for (domain,source),identity in links.items():
            if domain=='catalog':known.setdefault(tuple(str(x) for x in json.loads(source)),identity)
        added=0
        for row in rows:
            key=(str(row['inventory_id']),str(row['product_id']))
            if key in known:continue
            code=f"BASE-{row['inventory_id']}-{row['product_id']}"
            kind='group' if (row['inventory_id'],row['product_id']) in parents else 'sku'
            attrs={'base_product_key':':'.join(key),'source_sku':row['sku']}
            identity=db.execute('INSERT INTO company_products(code,name,parent_id,kind,attributes_json,active,version,updated_at) VALUES(?,?,NULL,?,?,1,1,?)',
                                (code,row['name'] or row['sku'] or code,kind,encode(attrs),now())).lastrowid
            known[key]=identity;added+=1
            record_history(db,'product',str(identity),{},dict(code=code,name=row['name'],kind=kind,source='Base 显式产品关系'),'Base 测试接入','初始化来源参考，不按名称猜测型号、颜色或跨目录归并',uuid.uuid4().hex)
            if kind=='sku' and ('catalog',encode([row['inventory_id'],row['product_id']])) not in links:
                db.execute('INSERT INTO product_source_links VALUES(?,?,?,?,?)',('catalog',encode([row['inventory_id'],row['product_id']]),identity,1,now()))
        for row in rows:
            key=(str(row['inventory_id']),str(row['product_id']));parent=known.get((str(row['inventory_id']),str(row['parent_id']))) if row['parent_id'] else None
            if parent:
                existing=db.execute('SELECT parent_id,attributes_json FROM company_products WHERE id=?',(known[key],)).fetchone()
                parent_node=db.execute('SELECT kind FROM company_products WHERE id=?',(parent,)).fetchone()
                # Only initialize untouched imported nodes. Manual moves are not overwritten.
                if parent_node['kind']=='group' and json.loads(existing['attributes_json']).get('base_product_key')==':'.join(key) and existing['parent_id'] is None:
                    db.execute('UPDATE company_products SET parent_id=? WHERE id=? AND version=1',(parent,known[key]))
    return added


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',type=Path,required=True)
    args=parser.parse_args()
    os.environ['DATA_PLATFORM_DB']=str(args.db.resolve())
    from app import main
    main.initialize()
    print(json.dumps({'company_nodes_added':import_tree()}))
