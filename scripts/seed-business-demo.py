"""Create a new isolated LOCAL demo database. Never calls Base or writes an existing DB."""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def seed(destination, anchor):
    if destination.exists():
        raise SystemExit('目标数据库已存在；请选择新的演示文件，禁止覆盖。')
    os.environ['DATA_PLATFORM_DB'] = str(destination.resolve())
    from app import main, ingestion, catalog, performance, allocations
    from app.management import GroupInput, EmployeeInput, create_group, create_employee
    from app.reports import ReportFilters, aggregate
    main.initialize()
    stamp = ingestion.now()
    groups = [create_group(GroupInput(code=f'DEMO-MP-G{i}', name=f'演示{i}组')) for i in range(1, 4)]
    employees = [create_employee(EmployeeInput(code=f'DEMO-MP-E{i+1}', name=f'演示人员{i+1}', group_id=groups[i//2]['id'])) for i in range(6)]
    with main.database() as db:
        db.execute("INSERT INTO app_metadata VALUES('data_mode','synthetic')")
        for platform in ('amazon','ebay','tiktok'):
            db.execute('INSERT INTO base_platforms VALUES(?,?)', (platform, stamp))
            for shop in ('1','2'):
                db.execute("INSERT INTO base_shops VALUES(?,?,?,'{}',?,1)", (platform,shop,f'DEMO-MP-{platform}-{shop}',stamp))
        db.execute("INSERT INTO base_catalogs VALUES(1,'DEMO-MP 隔离目录','{}',?,1)", (stamp,))
        for key,name,country in [('bl_pl','演示波兰仓','PL'),('bl_de','演示德国仓','DE')]:
            db.execute("INSERT INTO base_warehouses VALUES(?,'bl',0,?,?,?,1)", (key,name,json.dumps({'country':country}),stamp))
        db.execute("INSERT INTO base_products VALUES(1,90,0,'DEMO-MP-C51','演示 C51 父体','{}',?,1)", (stamp,))
        for identity,sku in [(100,'DEMO-MP-C51-BL-512-A'),(101,'DEMO-MP-C51-BL-512-B'),(102,'DEMO-MP-C51-BL-512-C'),(103,'DEMO-MP-C51-BL-512-SHARED'),(110,'DEMO-MP-C51-BL-128'),(111,'DEMO-MP-C51-BK-128'),(112,'DEMO-MP-C51-BK-512'),(120,'DEMO-MP-C61-BL-128'),(121,'DEMO-MP-C61-BK-256')]:
            db.execute("INSERT INTO base_products VALUES(1,?,90,?,?,'{}',?,1)", (identity,sku,sku,stamp))
        for product,warehouse,qty in [(100,'bl_pl','30'),(101,'bl_pl','30'),(102,'bl_pl','40'),(103,'bl_de','100'),(110,'bl_pl','20'),(111,'bl_de','15'),(112,'bl_pl','5'),(120,'bl_de','12'),(121,'bl_pl','0')]:
            db.execute("INSERT INTO base_stock VALUES(1,?,?,?,NULL,NULL,'{}',?)", (product,warehouse,qty,stamp))
    def product(code,name,parent=None,kind='group',**attrs):
        return catalog.save_product(catalog.ProductInput(code=code,name=name,parent_id=parent,kind=kind,attributes=attrs,expected_version=0,actor='演示初始化'))['id']
    c51=product('DEMO-MP-C51','C51');blue=product('DEMO-MP-C51-BL','C51 蓝色',c51);black=product('DEMO-MP-C51-BK','C51 黑色',c51)
    leaves={}
    for color,parent in [('BL',blue),('BK',black)]:
        for capacity in ('128','512'):
            leaves[color+capacity]=product(f'DEMO-MP-C51-{color}-{capacity}',f'C51-{"蓝色" if color=="BL" else "黑色"}-{capacity}G',parent,'sku',color=color,configuration=capacity+'G')
    c61=product('DEMO-MP-C61','C61');leaves['C61BL']=product('DEMO-MP-C61-BL-128','C61-蓝色-128G',c61,'sku');leaves['C61BK']=product('DEMO-MP-C61-BK-256','C61-黑色-256G',c61,'sku')
    mapping={100:leaves['BL512'],101:leaves['BL512'],102:leaves['BL512'],103:leaves['BL512'],110:leaves['BL128'],111:leaves['BK128'],112:leaves['BK512'],120:leaves['C61BL'],121:leaves['C61BK']}
    for source,node in mapping.items():
        catalog.save_link(catalog.LinkInput(domain='catalog',inventory_id=1,product_id=source,node_id=node,expected_version=0,actor='演示初始化'))
    start=anchor-timedelta(days=60)
    for index,platform in enumerate(('amazon','ebay','tiktok')):
        for shop in ('1','2'):
            if platform=='tiktok' and shop=='2':
                continue
            employee=employees[index*2+int(shop)-1]
            performance.save_rule(performance.RuleInput(scope='shop',platform=platform,shop_id=shop,employee_id=employee['id'],valid_from=start,expected_version=0,actor='演示初始化'))
    performance.save_rule(performance.RuleInput(scope='sku',node_id=leaves['BL512'],platform='amazon',shop_id='1',employee_id=employees[2]['id'],valid_from=start,expected_version=0,actor='演示初始化'))
    orders=[]
    source_ids=[100,110,111,112,120,121]
    for day in range(30):
        for sequence in range(2):
            index=day*2+sequence;platform=('amazon','ebay','tiktok')[index%3];shop=str(index%2+1)
            identity=900000+index;local=datetime.combine(anchor-timedelta(days=day),datetime.min.time(),ZoneInfo('Asia/Shanghai')).replace(hour=12)
            source=source_ids[index%len(source_ids)]
            items=[{'order_product_id':str(identity)+'-1','sku':f'DEMO-MP-{platform}-{source}','name':f'演示商品 {source}','quantity':index%3+1,'storage':'db','storage_id':1,'product_id':source}]
            if index%5==0:items.append({'order_product_id':str(identity)+'-2','sku':f'DEMO-MP-{platform}-110','name':'演示混合商品','quantity':2,'storage':'db','storage_id':1,'product_id':110})
            if index==59:items=[{'sku':'DEMO-MP-UNKNOWN','quantity':1}]
            orders.append({'order_id':identity,'external_order_id':f'DEMO-MP-{identity}','date_add':int(local.timestamp()),'date_confirmed':int(local.timestamp()),'confirmed':index%9!=0,'order_status_id':30 if index%13==0 else 20 if index%11==0 else 10,'order_source':'order_return' if index%17==0 else platform,'order_source_id':shop,'currency':'EUR','products':items})
    with main.database() as db:
        ingestion.save_order_page(db,orders,stamp)
        for row in orders:
            day=datetime.fromtimestamp(row['date_add'],ZoneInfo('Asia/Shanghai')).isoformat()
            db.execute("INSERT INTO order_annotations(order_id,extra_json,updated_at) VALUES(?,?,?)", (row['order_id'],json.dumps({'platform_order_date':day}),stamp))
    for source,warehouse,mode,shares in [(100,'bl_pl','encoded',[{'group_id':groups[0]['id'],'channels':[{'kind':'dedicated','platform':'amazon','quantity':20},{'kind':'dedicated','platform':'ebay','quantity':10}]}]),(101,'bl_pl','encoded',[{'group_id':groups[1]['id'],'channels':[{'kind':'shared','shared_platforms':['amazon','ebay'],'quantity':10}]}]),(102,'bl_pl','encoded',[{'group_id':groups[2]['id']}]),(103,'bl_de','split',[{'group_id':g['id'],'quantity':q} for g,q in zip(groups,[30,30,40])])]:
        with main.database() as db:
            row=db.execute('SELECT * FROM base_stock WHERE product_id=? AND warehouse_key=?',(source,warehouse)).fetchone();token=allocations.source_token(row)
        allocations.save_allocation(allocations.AllocationInput(inventory_id=1,product_id=source,warehouse_key=warehouse,mode=mode,stock_kind='physical',shares=shares,expected_version=0,expected_source_token=token,actor='演示初始化'))
    filters=ReportFilters(start=anchor-timedelta(days=29),end=anchor,granularity='company_product')
    report,_=aggregate(filters)
    expected={'synthetic':True,'anchor':str(anchor),'timezone':'Asia/Shanghai','filters':report['filters'],'sales':report,'inventory_example':{'company_node_id':leaves['BL512'],'PL':100,'DE':100,'total':200},'groups':groups,'employees':employees}
    destination.with_suffix('.expected.json').write_text(json.dumps(expected,ensure_ascii=False,indent=2))
    print(f'已生成隔离本地演示：{destination}；60 笔订单；未调用 Base。')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',type=Path,default=ROOT/'data'/'demo-business.sqlite3')
    parser.add_argument('--anchor',help='YYYY-MM-DD，默认上海当天')
    args=parser.parse_args()
    anchor=datetime.fromisoformat(args.anchor).date() if args.anchor else datetime.now(ZoneInfo('Asia/Shanghai')).date()
    seed(args.db,anchor)
