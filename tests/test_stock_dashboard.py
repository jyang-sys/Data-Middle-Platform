import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import main


class StockDashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(main, 'DB_PATH', Path(self.tmp.name)/'stock.db');self.db_patch.start()
        self.context = TestClient(main.app);self.client = self.context.__enter__()
        with main.database() as db:
            db.executemany("INSERT INTO base_catalogs VALUES(?,?, '{}','now',1)", [(1,'目录一'),(2,'目录二')])
            db.executemany("INSERT INTO base_warehouses VALUES(?,'bl',0,?,'{}','now',1)", [('bl_1','仓一'),('bl_2','仓二')])
            db.executemany("INSERT INTO base_products VALUES(?,?,0,?,?,'{}','now',1)", [(1,9,'SKU','产品'),(2,9,'SKU','另一个目录')])
            db.executemany("INSERT INTO base_stock VALUES(?,?,?,?,?,?,'{}',?)", [
                (1,9,'bl_1','10',None,'2','2026-10-01'), (1,9,'bl_2','-2','1',None,'2026-10-02'),
                (2,9,'bl_1','999','0','0','2026-10-03')])

    def tearDown(self):
        self.context.__exit__(None,None,None);self.db_patch.stop();self.tmp.cleanup()

    def test_catalogs_and_warehouses_never_silently_combined(self):
        result = self.client.get('/api/stock-dashboard/summary',params={'inventory_id':1}).json()
        self.assertEqual(result['total_rows'],2)
        self.assertEqual({r['stock']['value'] for r in result['warehouses']},{'10','-2'})
        self.assertIsNone(result['selected_warehouse'])
        self.assertIsNone(result['physical_stock']);self.assertIsNone(result['in_transit'])
        self.assertIsNone(result['available_stock']);self.assertIsNone(result['platform_saleable'])
        self.assertEqual(result['warehouses'][0]['reservations']['unknown_rows'],1)
        self.assertEqual(result['warehouses'][1]['negative_rows'],1)

    def test_filters_and_pagination_keep_source_values(self):
        result=self.client.get('/api/stock-dashboard/summary',params={'inventory_id':1,'warehouse_key':'bl_2','search':'SKU'}).json()
        self.assertEqual(result['total_rows'],1)
        empty=self.client.get('/api/stock-dashboard/summary',params={'inventory_id':1,'product_id':999}).json()
        self.assertEqual(empty['total_rows'],0)
        linked=self.client.get('/api/stock-dashboard/summary',params={'inventory_id':1,'product_id':9}).json()
        self.assertEqual(linked['total_rows'],2)
        self.assertEqual(result['rows'][0]['stock'],'-2')
        self.assertEqual(result['selected_warehouse']['stock']['value'],'-2')
        page=self.client.get('/api/stock-dashboard/summary',params={'inventory_id':1,'offset':1,'limit':1}).json()
        self.assertEqual(page['total_rows'],2);self.assertEqual(len(page['rows']),1)
        self.assertEqual(self.client.get('/api/stock-dashboard/summary',params={'inventory_id':999}).status_code,404)
        self.assertEqual(self.client.get('/api/stock-dashboard/summary').status_code,422)
