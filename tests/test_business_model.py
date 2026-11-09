import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app import main, ingestion


class BusinessModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(main, 'DB_PATH', Path(self.tmp.name)/'business.db')
        self.db_patch.start()
        self.context = TestClient(main.app)
        self.client = self.context.__enter__()
        self.params = {'start':'2026-11-01','end':'2026-11-30','timezone':'UTC','granularity':'company_product'}
        self.groups = [self.request('post','/api/management/groups',{'code':f'G{i}','name':f'组{i}'}) for i in range(3)]
        self.employees = [self.request('post','/api/management/employees',{'code':f'E{i}','name':f'员工{i}','group_id':self.groups[i//2]['id']}) for i in range(6)]
        with main.database() as db:
            for platform in ('amazon','ebay'):
                db.execute('INSERT INTO base_platforms VALUES(?,?)',(platform,'now'))
                for shop in ('1','2'):
                    db.execute("INSERT INTO base_shops VALUES(?,?,?,'{}','now',1)",(platform,shop,platform+shop))
            db.execute("INSERT INTO base_catalogs VALUES(1,'目录','{}','now',1)")
            for key in ('pl','de'):
                db.execute("INSERT INTO base_warehouses VALUES(?,'bl',0,?,'{}','now',1)",(key,key))
            for product,sku in [(10,'A'),(11,'B'),(12,'C'),(13,'SHARED'),(14,'OTHER')]:
                db.execute("INSERT INTO base_products VALUES(1,?,0,?,?,'{}','now',1)",(product,sku,sku))
            for product,warehouse,q in [(10,'pl','30'),(11,'pl','30'),(12,'pl','40'),(13,'de','100'),(14,'pl','9')]:
                db.execute("INSERT INTO base_stock VALUES(1,?,?,?,NULL,NULL,'{}','now')",(product,warehouse,q))
        self.root=self.product('C51')
        self.blue=self.product('C51-BL',self.root['id'])
        self.leaf=self.product('C51-BL-512',self.blue['id'],'sku')
        self.other=self.product('C51-BL-128',self.blue['id'],'sku')
        for source in (10,11,12,13):
            self.request('put','/api/catalog/links',{'domain':'catalog','inventory_id':1,'product_id':source,'node_id':self.leaf['id'],'expected_version':0,'actor':'验收'})
        self.request('put','/api/catalog/links',{'domain':'catalog','inventory_id':1,'product_id':14,'node_id':self.other['id'],'expected_version':0,'actor':'验收'})

    def tearDown(self):
        self.context.__exit__(None,None,None)
        self.db_patch.stop()
        self.tmp.cleanup()

    def request(self,method,path,body=None,status=None):
        response=getattr(self.client,method)(path,json=body) if body is not None else getattr(self.client,method)(path)
        self.assertEqual(response.status_code,status or (201 if method=='post' and path in ('/api/management/groups','/api/management/employees','/api/auth/setup') else 200),response.text)
        return response.json()

    def product(self,code,parent=None,kind='group'):
        return self.request('put','/api/catalog/products',{'code':code,'name':code,'parent_id':parent,'kind':kind,'expected_version':0,'actor':'验收'})

    def seed_order(self,identity,items=None,**extra):
        row={'order_id':identity,'date_add':int(datetime(2026,11,5,12,tzinfo=timezone.utc).timestamp()),'confirmed':True,'order_status_id':10,'order_source':'amazon','order_source_id':1,'products':items or [{'sku':'A','quantity':2,'storage':'db','storage_id':1,'product_id':10}],**extra}
        with main.database() as db:
            ingestion.save_order_page(db,[row],ingestion.now())

    def rule(self,employee,**changes):
        return self.request('put','/api/performance/rules',{'scope':'sku','node_id':self.leaf['id'],'employee_id':employee,'valid_from':'2026-01-01','expected_version':0,'actor':'验收',**changes})

    def allocation(self,product=13,mode='split',shares=None,kind='physical',**changes):
        view=self.client.get('/api/allocations/view').json()
        row=next(r for r in view['rows'] if r['product_id']==product)
        body={'inventory_id':1,'product_id':product,'warehouse_key':row['warehouse_key'],'mode':mode,'stock_kind':kind,'shares':shares if shares is not None else [{'group_id':g['id'],'quantity':q} for g,q in zip(self.groups,[30,30,40])],'expected_version':row['allocation_version'],'expected_source_token':row['source_token'],'actor':'验收',**changes}
        return self.client.put('/api/allocations/source',json=body)

    def report(self,**changes):
        response=self.client.get('/api/reports/sales',params={**self.params,**changes})
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def test_tree_rollup_scoped_links_and_cycle_protection(self):
        self.seed_order(1)
        self.seed_order(2,[{'sku':'OTHER','quantity':3,'storage':'db','storage_id':1,'product_id':11}],order_source='ebay')
        report=self.report(node_id=self.root['id'])
        self.assertEqual(report['total_units'],5)
        self.assertEqual(len(report['monthly']),1)
        self.assertEqual(report['comparisons']['hierarchy'][0]['quantity'],5)
        self.assertEqual(report['comparisons']['hierarchy'][0]['children'][0]['children'][0]['quantity'],5)
        self.assertEqual(sum(r['quantity'] for r in report['comparisons']['platforms']),5)
        self.assertEqual(sum(s['quantity'] for p in report['comparisons']['products'] for p in p['children'] for s in p['children']),5)
        conflict=self.client.put('/api/catalog/products',json={'id':self.root['id'],'code':'C51','name':'C51','parent_id':self.blue['id'],'kind':'group','expected_version':1,'actor':'验收'})
        self.assertEqual(conflict.status_code,422)
        self.assertEqual(self.client.put('/api/catalog/links',json={'domain':'catalog','inventory_id':1,'product_id':10,'node_id':self.root['id'],'expected_version':1,'actor':'验收'}).status_code,422)
        self.assertEqual(self.client.put('/api/catalog/products',json={'id':self.leaf['id'],'code':'C51-BL-512','name':'changed','kind':'sku','expected_version':0,'actor':'验收'}).status_code,409)

    def test_order_line_rules_freeze_and_resync_does_not_reassign(self):
        general=self.rule(self.employees[0]['id'],scope='shop',node_id=None,platform='amazon',shop_id='1')
        specific=self.rule(self.employees[2]['id'],platform='amazon',shop_id='1')
        mixed=[{'sku':'A','quantity':2,'storage':'db','storage_id':1,'product_id':10},{'sku':'OTHER','quantity':3,'storage':'db','storage_id':1,'product_id':14}]
        self.seed_order(1,mixed)
        self.assertEqual(self.report(group_id=self.groups[0]['id'])['total_units'],3)
        self.assertEqual(self.report(group_id=self.groups[1]['id'])['total_units'],2)
        self.rule(self.employees[4]['id'],id=specific['id'],expected_version=1,platform='amazon',shop_id='1')
        self.seed_order(1,mixed)
        self.assertEqual(self.report(group_id=self.groups[1]['id'])['total_units'],2)
        self.assertEqual(self.report(group_id=self.groups[2]['id'])['total_units'],0)
        self.seed_order(2,mixed)
        self.assertEqual(self.report(group_id=self.groups[2]['id'])['total_units'],2)
        with main.database() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM ownership_history WHERE entity_type='line_owner'").fetchone()[0],4)
        details=self.client.get('/api/reports/details',params={**self.params,'group_key_json':json.dumps(['id',self.groups[1]['id']])}).json()
        self.assertEqual(details['total_units'],2)
        self.assertEqual(details['lines'][0]['ownership_source'],'snapshot')
        self.assertEqual(self.client.get('/api/reports/details',params={**self.params,'group_key_json':'["id",true]'}).status_code,422)
        self.assertEqual(self.client.put('/api/performance/rules',json={'scope':'shop','platform':'amazon','shop_id':'1','employee_id':self.employees[0]['id'],'valid_from':'2026-02-01','expected_version':0,'actor':'验收'}).status_code,409)

    def test_historical_preview_requires_unchanged_data_and_explicit_apply(self):
        self.seed_order(1)
        self.rule(self.employees[0]['id'])
        self.assertEqual(self.report()['groups'][0]['name'],'未分配')
        self.assertEqual(self.report(ownership_basis='rules')['groups'][0]['id'],self.groups[0]['id'])
        body={'filters':self.params,'actor':'验收'}
        first=self.request('post','/api/performance/materialize',body)
        self.seed_order(1,[{'sku':'A','quantity':3,'storage':'db','storage_id':1,'product_id':10}])
        self.assertEqual(self.client.post('/api/performance/materialize',json={**body,'expected_token':first['token']}).status_code,409)
        second=self.request('post','/api/performance/materialize',body)
        saved=self.request('post','/api/performance/materialize',{**body,'expected_token':second['token']})
        self.assertTrue(saved['saved'])
        self.assertEqual(self.report(group_id=self.groups[0]['id'])['total_units'],3)

    def test_encoded_and_split_inventory_reconcile_without_double_count(self):
        for product,group in zip((10,11,12),self.groups):
            response=self.allocation(product,'encoded',[{'group_id':group['id']}]);self.assertEqual(response.status_code,200,response.text)
        response=self.allocation();self.assertEqual(response.status_code,200,response.text)
        view=self.client.get('/api/allocations/view',params={'node_id':self.leaf['id']}).json()
        self.assertEqual(view['products'][0]['physical_total'],'200')
        self.assertEqual({k:v['physical'] for k,v in view['products'][0]['warehouses'].items()},{'de':'100','pl':'100'})
        self.assertEqual(sum(int(r['stock']) for r in view['rows']),200)
        group=self.client.get('/api/allocations/view',params={'group_id':self.groups[0]['id'],'node_id':self.root['id']}).json()
        self.assertEqual(sum(int(r['stock'] or 0) for r in group['rows']),60)
        invalid=self.allocation(shares=[{'group_id':self.groups[0]['id'],'quantity':101}])
        self.assertEqual(invalid.status_code,422)
        before=next(r for r in view['rows'] if r['product_id']==13)
        with main.database() as db:db.execute("UPDATE base_stock SET stock='90',fetched_at='later' WHERE product_id=13")
        current=self.client.get('/api/allocations/view',params={'node_id':self.leaf['id']}).json()
        row=next(r for r in current['rows'] if r['product_id']==13)
        self.assertEqual([s['quantity'] for s in row['shares']],[30,30,40])
        self.assertEqual(row['difference'],'-10');self.assertTrue(row['allocation_conflict'])
        stale={'inventory_id':1,'product_id':13,'warehouse_key':'de','mode':'split','stock_kind':'physical','shares':[],'expected_version':before['allocation_version'],'expected_source_token':before['source_token'],'actor':'验收'}
        self.assertEqual(self.client.put('/api/allocations/source',json=stale).status_code,409)

    def test_shared_pool_is_not_repeated_in_platform_totals(self):
        shares=[{'group_id':self.groups[0]['id'],'quantity':30,'channels':[{'kind':'dedicated','platform':'amazon','quantity':20},{'kind':'dedicated','platform':'ebay','quantity':10}]},{'group_id':self.groups[1]['id'],'quantity':30,'channels':[{'kind':'shared','shared_platforms':['amazon','ebay'],'quantity':15}]}]
        self.assertEqual(self.allocation(shares=shares).status_code,200)
        all_rows=self.client.get('/api/allocations/view').json()['rows'];row=next(r for r in all_rows if r['product_id']==13)
        self.assertEqual(row['unassigned_quantity'],'40')
        amazon=self.client.get('/api/allocations/view',params={'platform':'amazon'}).json()
        ebay=self.client.get('/api/allocations/view',params={'platform':'ebay'}).json()
        self.assertEqual(sum(int(r['stock']) for r in amazon['rows']),20)
        self.assertEqual(sum(int(r['stock']) for r in ebay['rows']),10)
        self.assertEqual(row['shares'][1]['channels'][0]['quantity'],15)
        shares[0]['channels'][0]['quantity']=31
        self.assertEqual(self.allocation(shares=shares).status_code,422)
        self.assertEqual(self.allocation(shares=shares,kind='return_pending').status_code,422)

    def test_source_identity_change_does_not_reuse_old_line_owner(self):
        self.rule(self.employees[0]['id'])
        self.seed_order(1)
        self.seed_order(1,[{'sku':'OTHER','quantity':5,'storage':'db','storage_id':1,'product_id':14}])
        self.assertEqual(self.report()['groups'][0]['name'],'未分配')
        current=self.request('get','/api/performance/lines/1/0')
        self.assertFalse(current['snapshot_valid']);self.assertIsNone(current['employee_id'])

    def test_manual_line_correction_overrides_order_and_can_explicitly_clear(self):
        self.rule(self.employees[0]['id'])
        self.seed_order(1)
        self.request('patch','/api/management/orders/1/ownership',{'employee_id':self.employees[2]['id'],'expected_version':0,'actor':'验收'})
        row=self.request('get','/api/performance/lines/1/0')
        body={'employee_id':self.employees[4]['id'],'expected_version':row['version'],'expected_fingerprint':row['fingerprint'],'actor':'验收'}
        self.request('put','/api/performance/lines/1/0',body)
        self.assertEqual(self.report(group_id=self.groups[2]['id'])['total_units'],2)
        self.assertEqual(self.client.put('/api/performance/lines/1/0',json=body).status_code,409)
        row=self.request('get','/api/performance/lines/1/0')
        self.request('put','/api/performance/lines/1/0',{'employee_id':None,'expected_version':row['version'],'expected_fingerprint':row['fingerprint'],'actor':'验收'})
        self.assertEqual(self.report()['groups'][0]['name'],'未分配')

    def test_server_roles_filter_aggregates_details_export_and_inventory(self):
        self.rule(self.employees[0]['id'])
        self.seed_order(1)
        self.seed_order(2,[{'sku':'OTHER','quantity':8,'storage':'db','storage_id':1,'product_id':14}])
        self.request('patch','/api/management/orders/2/ownership',{'employee_id':self.employees[2]['id'],'expected_version':0,'actor':'验收'})
        self.assertEqual(self.allocation().status_code,200)
        password='test-password-12345'
        self.request('post','/api/auth/setup',{'username':'admin','password':password})
        self.request('post','/api/auth/login',{'username':'admin','password':password})
        for role,username,group,employee in [('owner','boss',None,None),('manager','leader',self.groups[0]['id'],None),('operator','staff',self.groups[0]['id'],self.employees[0]['id'])]:
            self.request('put','/api/auth/users',{'username':username,'password':password,'name':username,'role':role,'group_id':group,'employee_id':employee})
        self.request('post','/api/auth/logout')
        self.assertEqual(self.client.get('/api/reports/sales',params=self.params).status_code,401)
        self.request('post','/api/auth/login',{'username':'boss','password':password})
        self.assertEqual(self.report()['total_units'],10)
        self.assertEqual(self.client.get('/api/base/orders').status_code,403)
        self.request('post','/api/auth/logout')
        for username in ('leader','staff'):
            self.request('post','/api/auth/login',{'username':username,'password':password})
            self.assertEqual(self.report()['total_units'],2)
            self.assertEqual(self.report(group_id=self.groups[1]['id'])['total_units'],0)
            detail=self.client.get('/api/reports/details',params=self.params).json()
            self.assertEqual({r['order_id'] for r in detail['lines']},{1})
            wb=load_workbook(io.BytesIO(self.client.get('/api/reports/export',params=self.params).content))
            self.assertEqual(sum(r[12] for r in list(wb['订单明细'].values)[1:]),2)
            stock=self.client.get('/api/allocations/view').json()
            self.assertEqual(len(stock['rows']),1)
            self.assertEqual(stock['rows'][0]['stock'],'30')
            self.assertNotIn('source_stock',stock['rows'][0]);self.assertNotIn('source_token',stock['rows'][0])
            self.assertEqual([s['group_id'] for s in stock['rows'][0]['shares']],[self.groups[0]['id']])
            raw=self.client.get('/api/stock-dashboard/dataset',params={'inventory_id':1}).json()
            self.assertEqual(raw['rows'][0]['stock'],'30')
            self.assertEqual(self.client.get('/api/stock-dashboard/summary',params={'inventory_id':1}).status_code,403)
            self.assertEqual(self.client.get('/api/base/orders/2').status_code,403)
            self.assertEqual(self.client.get('/api/performance/rules').status_code,403)
            self.assertEqual(self.client.put('/api/allocations/source',json={}).status_code,403)
            self.request('post','/api/auth/logout')

    def test_auth_rate_limit_and_last_admin_protection(self):
        password='test-password-12345'
        self.assertEqual(self.client.post('/api/auth/setup',json={'username':'   ','password':password}).status_code,422)
        admin=self.request('post','/api/auth/setup',{'username':'admin','password':password})
        for _ in range(10):self.assertEqual(self.client.post('/api/auth/login',json={'username':'admin','password':'incorrect-password'}).status_code,401)
        self.assertEqual(self.client.post('/api/auth/login',json={'username':'admin','password':password}).status_code,429)
        with main.database() as db:db.execute('DELETE FROM login_attempts')
        self.request('post','/api/auth/login',{'username':'admin','password':password})
        self.assertEqual(self.client.put('/api/auth/users',json={'id':admin['id'],'username':'admin','name':'admin','password':password,'role':'owner'}).status_code,409)
        response=self.client.get('/api/auth/users')
        self.assertNotIn('password_hash',response.text)

    def test_demo_database_rejects_real_sync_and_required_login_can_be_enforced(self):
        with main.database() as db:db.execute("INSERT INTO app_metadata VALUES('data_mode','synthetic')")
        with patch.object(ingestion,'call') as upstream:
            self.assertEqual(self.client.post('/api/base/sync/orders').status_code,409)
            self.assertEqual(self.client.post('/api/sales/sync').status_code,409)
            with self.assertRaises(Exception) as result:ingestion.run_sync('orders')
            self.assertEqual(result.exception.status_code,409)
            upstream.assert_not_called()
        self.assertEqual(self.client.get('/api/auth/session').json()['data_mode'],'synthetic')
        with patch.dict('os.environ',{'APP_REQUIRE_LOGIN':'1'}):
            self.assertEqual(self.client.get('/api/reports/sales').status_code,401)
            self.assertEqual(self.client.get('/api/auth/session').status_code,200)
