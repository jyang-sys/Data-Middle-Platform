import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from app import main, ingestion, worker


def source_order(order_id, quantity=2, **extra):
    return {"order_id": order_id, "date_add": 1700000000, "date_confirmed": 0,
            "confirmed": False, "order_source": "amazon", "order_source_id": 1,
            "products": [{"sku": "C51-BLUE", "quantity": quantity}],
            "custom_extra_fields": {"123": "负责人来源字段"}, "future_field": {"kept": True}, **extra}


class IngestionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(main, "DB_PATH", Path(self.tmp.name) / "test.db")
        self.db_patch.start()
        self.context = TestClient(main.app)
        self.client = self.context.__enter__()

    def tearDown(self):
        self.context.__exit__(None, None, None)
        self.db_patch.stop()
        self.tmp.cleanup()

    def synchronize_orders(self, pages):
        with patch.object(ingestion, "call", side_effect=[{"orders": page} for page in pages]) as mock:
            result = self.client.post("/api/base/sync/orders")
        return result, mock

    def test_all_fields_unconfirmed_and_annotation_survive_refresh(self):
        result, mock = self.synchronize_orders([[source_order(1)], []])
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(mock.call_args_list[0].args[1]["get_unconfirmed_orders"])
        self.assertTrue(mock.call_args_list[0].args[1]["include_custom_extra_fields"])
        body = {"sales_owner": "张三", "business_group": "一组", "extra_fields": {"活动": "双十一"}}
        result = self.client.patch("/api/base/orders/1/annotation", json=body)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.client.patch("/api/base/orders/1/annotation", json={"sales_owner": "李四"}).json()["business_group"], "一组")
        self.synchronize_orders([[source_order(1, 5, new_field=9)], []])
        data = self.client.get("/api/base/orders/1").json()
        self.assertEqual(data["source_order"]["new_field"], 9)
        self.assertEqual(data["source_order"]["future_field"], {"kept": True})
        self.assertEqual(data["source_order"]["custom_extra_fields"], {"123": "负责人来源字段"})
        self.assertEqual(data["annotation"]["sales_owner"], "李四")
        self.assertEqual(data["annotation"]["extra_fields"], body["extra_fields"])
        with main.database() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM base_orders").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT quantity FROM base_order_items").fetchone()[0], "5")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM sales_orders").fetchone()[0], 0)

    def test_id_pagination_more_than_100_same_second_orders(self):
        result, mock = self.synchronize_orders([[source_order(i) for i in range(1, 101)], [source_order(101)], []])
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(mock.call_args_list[1].args[1]["id_from"], 101)
        self.assertEqual(mock.call_args_list[2].args[1]["id_from"], 102)
        self.assertEqual(self.client.get("/api/base/status").json()["counts"]["orders"], 101)

    def test_failure_records_partial_progress_and_retry_is_idempotent(self):
        from fastapi import HTTPException
        with patch.object(ingestion, "call", side_effect=[{"orders": [source_order(1)]}, HTTPException(502, "failed")]):
            result = self.client.post("/api/base/sync/orders")
        self.assertEqual(result.status_code, 502)
        job = self.client.get("/api/base/status").json()["jobs"][0]
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["records"], 1)
        self.assertEqual(job["cursor"], 2)
        self.synchronize_orders([[source_order(1), source_order(2)], []])
        self.assertEqual(self.client.get("/api/base/status").json()["counts"]["orders"], 2)

    def test_invalid_page_rolls_back_and_overlap_is_rejected(self):
        invalid = source_order(2); invalid["products"][0]["quantity"] = "NaN"
        result, _ = self.synchronize_orders([[source_order(1), invalid]])
        self.assertEqual(result.status_code, 502)
        self.assertEqual(self.client.get("/api/base/status").json()["counts"]["orders"], 0)
        with ingestion.sync_job("orders"):
            self.assertEqual(self.client.post("/api/base/sync/orders").status_code, 409)
        self.assertEqual(self.client.patch("/api/base/orders/999/annotation", json={}).status_code, 404)

    def catalog_responses(self, stock=6):
        parent = {"parent_id": 0, "sku": "C51", "text_fields": {"name": "C51"}, "stock": {}}
        child = {"parent_id": 10, "sku": "C51-BLUE", "text_fields": {"name": "C51 蓝色"},
                 "stock": {"bl_7": stock}, "incoming": {"bl_7": 2}}
        return [{"inventories": [{"inventory_id": 3, "name": "目录"}]},
                {"products": {"10": parent, "11": child}},
                {"products": {"10": parent, "11": child}}, {"products": {}}]

    def test_product_variants_and_parent_sku(self):
        with patch.object(ingestion, "call", side_effect=self.catalog_responses()) as mock:
            result = self.client.post("/api/base/sync/products")
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(mock.call_args_list[1].args[1]["include_variants"])
        rows = self.client.get("/api/base/products").json()
        self.assertEqual(rows[1]["parent_sku"], "C51")
        self.assertEqual(rows[1]["child_sku"], "C51-BLUE")
        with patch.object(ingestion, "call", side_effect=[{"inventories": []}]):
            self.client.post("/api/base/sync/products")
        self.assertEqual(self.client.get("/api/base/products").json()[1]["active"], 0)

    def test_inventory_unknown_reservations_and_atomic_snapshot(self):
        data = self.catalog_responses()
        data.insert(1, {"warehouses": [{"warehouse_type": "bl", "warehouse_id": 7, "name": "海外仓"}]})
        with patch.object(ingestion, "call", side_effect=data):
            result = self.client.post("/api/base/sync/inventory")
        self.assertEqual(result.status_code, 200, result.text)
        row = self.client.get("/api/base/inventory").json()[0]
        self.assertEqual(row["stock"], "6")
        self.assertEqual(row["incoming"], "2")
        self.assertIsNone(row["reservations"])
        failing = self.catalog_responses(9)
        failing.insert(1, data[1]); failing[-1] = {"invalid": []}
        with patch.object(ingestion, "call", side_effect=failing):
            result = self.client.post("/api/base/sync/inventory")
        self.assertEqual(result.status_code, 502)
        self.assertEqual(self.client.get("/api/base/inventory").json()[0]["stock"], "6")

    def test_shop_ids_are_scoped_by_platform(self):
        with patch.object(ingestion, "call", return_value={"sources": {"amazon": {"1": "A"}, "ebay": {"1": "B"}}}):
            self.assertEqual(self.client.post("/api/base/sync/shops").status_code, 200)
        self.assertEqual(len(self.client.get("/api/base/shops").json()), 2)

    def test_worker_attempts_every_domain_despite_failure(self):
        with patch.object(worker, "run_sync", side_effect=[RuntimeError(), "p", "i", "o"]) as mock:
            self.assertFalse(worker.cycle())
        self.assertEqual([x.args[0] for x in mock.call_args_list], list(ingestion.DOMAINS))

    def test_order_pagination_stall_is_failure(self):
        result, _ = self.synchronize_orders([[source_order(1)], [source_order(1)]])
        self.assertEqual(result.status_code, 502)
        self.assertEqual(self.client.get("/api/base/status").json()["jobs"][0]["status"], "failed")

    def test_partial_order_without_products_is_preserved_then_completed(self):
        partial = source_order(1)
        del partial["products"]
        result, _ = self.synchronize_orders([[partial], []])
        self.assertEqual(result.status_code, 200, result.text)
        self.assertNotIn("products", self.client.get("/api/base/orders/1").json()["source_order"])
        self.synchronize_orders([[source_order(1)], []])
        with main.database() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM base_order_items").fetchone()[0], 1)
