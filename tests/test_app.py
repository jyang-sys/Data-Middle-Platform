import io
import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from openpyxl import Workbook

from app import main, sales


def order(order_id=1, currency="PLN", price="10.10", quantity=3, sku="SKU-001", source="amazon", shop=10):
    return {"order_id": order_id, "date_confirmed": int(time.time()), "currency": currency,
            "delivery_price": "5.00", "order_status_id": 1,
            "order_source": source, "order_source_id": shop,
            "products": [{"name": "测试商品", "sku": sku, "price_brutto": price, "quantity": quantity}]}


class AppTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(main, "DB_PATH", Path(self.directory.name) / "test.sqlite3")
        self.db_patch.start()
        self.sources_patch = patch.object(sales, "get_sources", return_value=[])
        self.sources_patch.start()
        self.client_context = TestClient(main.app)
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        self.db_patch.stop()
        self.sources_patch.stop()
        self.directory.cleanup()

    def upload(self, text="地区,销售额\n华东,12.50\n华南,8.00\n"):
        return self.client.post("/api/datasets", files={"file": ("sales.csv", text.encode(), "text/csv")})

    def test_health_and_frontend(self):
        self.assertEqual(self.client.get("/api/health").json(), {"status": "ok"})
        self.assertIn("销售分析", self.client.get("/").text)
        self.assertEqual(self.client.get("/static/app.js").status_code, 200)

    def test_csv_query_preview_delete(self):
        result = self.upload()
        self.assertEqual(result.status_code, 201, result.text)
        dataset = result.json()
        self.assertEqual(self.client.get(f'/api/datasets/{dataset["id"]}/preview').json()["rows"][0], ["华东", "12.50"])
        query = self.client.post("/api/query", json={"sql": f'SELECT SUM(CAST("销售额" AS REAL)) FROM "{dataset["table_name"]}"'})
        self.assertEqual(query.json()["rows"], [[20.5]])
        self.assertEqual(self.client.delete(f'/api/datasets/{dataset["id"]}').status_code, 200)
        self.assertEqual(self.client.get(f'/api/datasets/{dataset["id"]}/preview').status_code, 404)
        self.assertEqual(self.client.get("/api/datasets").json(), [])

    def test_xlsx_import(self):
        workbook = Workbook()
        workbook.active.append(["SKU", "销量"])
        workbook.active.append(["A-1", 8])
        content = io.BytesIO(); workbook.save(content)
        response = self.client.post("/api/datasets", files={"file": ("sales.xlsx", content.getvalue())})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["row_count"], 1)

    def test_invalid_uploads_rollback_schema(self):
        for text in ["", "a,A\n1,2", "a,b\n1,2\n3", "a,b\n", ",b\n1,2"]:
            self.assertEqual(self.upload(text).status_code, 400)
        with main.database() as connection:
            count = connection.execute("SELECT count(*) FROM sqlite_master WHERE name LIKE 'data_%' AND name != 'datasets'").fetchone()[0]
        self.assertEqual(count, 0)
        self.assertEqual(self.client.get("/api/datasets").json(), [])

    def test_query_cannot_mutate_or_read_metadata(self):
        dataset = self.upload().json()
        for sql in [f'DELETE FROM "{dataset["table_name"]}"', 'DROP TABLE datasets',
                    'SELECT * FROM datasets', 'SELECT * FROM sqlite_master', 'PRAGMA database_list',
                    "ATTACH DATABASE ':memory:' AS extra", "SELECT load_extension('anything')"]:
            response = self.client.post("/api/query", json={"sql": sql})
            self.assertEqual(response.status_code, 400, sql)
        self.assertEqual(self.client.get("/api/overview").json()["rows"], 2)

    def test_cross_origin_mutations_rejected(self):
        response = self.client.post("/api/sales/demo", headers={"Origin": "https://untrusted.example"})
        self.assertEqual(response.status_code, 403)

    def test_query_result_limit_and_allocation_limit(self):
        dataset = self.upload("value\n" + "1\n" * 250).json()
        response = self.client.post("/api/query", json={"sql": f'SELECT * FROM "{dataset["table_name"]}"'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["rows"]), 200)
        self.assertTrue(response.json()["truncated"])
        response = self.client.post("/api/query", json={"sql": "SELECT randomblob(2000000)"})
        self.assertEqual(response.status_code, 400)

    def test_money_currency_and_dimensions(self):
        sales.save_orders("baselinker", [order(), order(2, "EUR", quantity=7), order(3, price="2.00", quantity=2, sku="SKU-002", source="shop", shop=20)])
        data = self.client.get("/api/sales/dashboard?currency=PLN").json()
        self.assertEqual(data["currencies"], ["EUR", "PLN"])
        self.assertEqual(data["orders"], 2)
        self.assertEqual(data["units"], 5)
        self.assertEqual(data["revenue"], 44.3)
        self.assertEqual(data["average_order"], 22.15)
        self.assertEqual(data["products"][0]["sku"], "SKU-001")
        self.assertEqual({row["name"] for row in data["platforms"]}, {"amazon", "shop"})
        self.assertEqual(sum(row["quantity"] for row in data["shops"]), 5)

    def test_sync_paginated_and_idempotent(self):
        timestamp = int(time.time()) - 10
        first_page = [order(i) for i in range(1, 101)]
        for item in first_page:
            item["date_confirmed"] = timestamp
        next_order = order(101)
        next_order["date_confirmed"] = timestamp + 1
        with patch.object(sales, "api_call", side_effect=[first_page, [first_page[-1], next_order]]) as api_mock, patch.object(sales.time, "sleep"):
            result = self.client.post("/api/sales/sync", json={"days": 30})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()["orders"], 101)
            self.assertEqual(api_mock.call_args_list[1].args[0]["date_confirmed_from"], timestamp)
        with patch.object(sales, "api_call", side_effect=[[order(1, quantity=5)], []]), patch.object(sales.time, "sleep"):
            self.assertEqual(self.client.post("/api/sales/sync", json={"days": 30}).status_code, 200)
        result = self.client.get("/api/sales/dashboard").json()
        self.assertEqual(result["orders"], 101)
        self.assertEqual(result["units"], 305)

    def test_sync_failure_preserves_previous_data(self):
        sales.save_orders("baselinker", [order()])
        invalid = order(3); invalid["products"][0]["price_brutto"] = "invalid"
        with patch.object(sales, "api_call", side_effect=[[order(2), invalid]]), patch.object(sales.time, "sleep"):
            self.assertEqual(self.client.post("/api/sales/sync", json={"days": 30}).status_code, 502)
        self.assertEqual(self.client.get("/api/sales/dashboard").json()["orders"], 1)

    def test_nonadvancing_pagination_rejected(self):
        full_page = [order(i) for i in range(1, 101)]
        timestamp = int(time.time())
        for item in full_page:
            item["date_confirmed"] = timestamp
        with patch.object(sales, "api_call", side_effect=[full_page, full_page]), patch.object(sales.time, "sleep"):
            self.assertEqual(self.client.post("/api/sales/sync", json={"days": 30}).status_code, 422)
        self.assertEqual(self.client.get("/api/sales/dashboard").json()["orders"], 0)

    def test_missing_token_is_clear_and_safe(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(self.client.get("/api/sales/connection").json()["configured"])
            self.assertEqual(self.client.post("/api/sales/sync", json={"days": 30}).status_code, 503)

    def test_baselinker_wire_format(self):
        payload = io.BytesIO(json.dumps({"status": "SUCCESS", "orders": [order()]}).encode())
        with patch.dict(os.environ, {"BASELINKER_API_TOKEN": "test-only-placeholder"}), patch.object(sales.urllib.request, "urlopen", return_value=payload) as request:
            self.assertEqual(len(sales.api_call({"id_from": 10})), 1)
            sent = request.call_args.args[0]
            self.assertEqual(sent.full_url, "https://api.baselinker.com/connector.php")
            body = sales.urllib.parse.parse_qs(sent.data.decode())
            self.assertEqual(body["method"], ["getOrders"])
            self.assertEqual(json.loads(body["parameters"][0]), {"id_from": 10})
            self.assertEqual(sent.get_header("X-bltoken"), "test-only-placeholder")

    def test_bad_token_reports_action_without_exposing_upstream_message(self):
        payload = io.BytesIO(json.dumps({"status": "ERROR", "error_code": "ERROR_BAD_TOKEN", "error_message": "private diagnostic"}).encode())
        with patch.dict(os.environ, {"BASELINKER_API_TOKEN": "test-only-placeholder"}), patch.object(sales.urllib.request, "urlopen", return_value=payload):
            response = self.client.post("/api/sales/sync", json={"days": 7})
        self.assertEqual(response.status_code, 502)
        self.assertIn("ERROR_BAD_TOKEN", response.text)
        self.assertNotIn("private diagnostic", response.text)

    def test_shop_names_use_platform_and_id_together(self):
        sales.save_orders("baselinker", [order(1, source="amazon", shop=10), order(2, source="shop", shop=10)],
                          sources=[("amazon", "10", "Amazon 店铺"), ("shop", "10", "独立站")])
        data = self.client.get("/api/sales/dashboard").json()
        self.assertEqual({item["name"] for item in data["shops"]}, {"Amazon 店铺", "独立站"})
        self.assertEqual(len(data["shops"]), 2)

    def test_source_documented_indexed_list(self):
        payload = io.BytesIO(json.dumps({"status": "SUCCESS", "sources": {"shop": {"1": "店铺"}, "order_return": ["Order return"]}}).encode())
        # Invoke the real implementation while the default mock keeps other tests offline.
        self.sources_patch.stop()
        try:
            with patch.dict(os.environ, {"BASELINKER_API_TOKEN": "test-only-placeholder"}), patch.object(sales.urllib.request, "urlopen", return_value=payload):
                sources = sales.get_sources()
            self.assertIn(("shop", "1", "店铺"), sources)
            self.assertIn(("order_return", "0", "Order return"), sources)
        finally:
            self.sources_patch.start()

    def test_demo_separate_repeatable_and_persistent(self):
        sales.save_orders("baselinker", [order()])
        for _ in range(2):
            self.assertEqual(self.client.post("/api/sales/demo").json()["orders"], 120)
        demo = self.client.get("/api/sales/dashboard?source=demo").json()
        self.assertEqual(demo["orders"], 120)
        self.assertEqual(self.client.get("/api/sales/dashboard").json()["orders"], 1)
        main.initialize()
        self.assertEqual(self.client.get("/api/sales/dashboard?source=demo").json()["orders"], 120)

    def test_invalid_ranges_rejected(self):
        self.assertEqual(self.client.post("/api/sales/sync", json={"days": 0}).status_code, 422)
        self.assertEqual(self.client.get("/api/sales/dashboard?source=bad").status_code, 422)


if __name__ == "__main__":
    unittest.main()
