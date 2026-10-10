import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from app import main, ingestion


def source_order(order_id, **extra):
    return {"order_id": order_id, "order_source": "amazon", "order_source_id": 1,
            "confirmed": True, "currency": "PLN", "products": [{"quantity": 2, "sku": "SKU"}],
            "original_future_field": {"preserve": True}, **extra}


class ManagementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.db_patch = patch.object(main, "DB_PATH", self.db_path)
        self.db_patch.start()
        self.context = TestClient(main.app)
        self.client = self.context.__enter__()
        with main.database() as db:
            ingestion.save_order_page(db, [source_order(1), source_order(2), source_order(3)], ingestion.now())
            db.execute("INSERT INTO base_shops(platform,shop_id,name,raw_json,updated_at) VALUES('amazon','1','A','{}',?)", (ingestion.now(),))
            db.execute("INSERT INTO base_shops(platform,shop_id,name,raw_json,updated_at) VALUES('ebay','1','B','{}',?)", (ingestion.now(),))
        self.group = self.client.post("/api/management/groups", json={"code": "G1", "name": "一组"}).json()
        self.employee = self.client.post("/api/management/employees", json={"code": "E1", "name": "张三", "group_id": self.group["id"]}).json()

    def tearDown(self):
        self.context.__exit__(None, None, None)
        self.db_patch.stop()
        self.tmp.cleanup()

    def state(self, order_id=1):
        return self.client.get(f"/api/management/orders/{order_id}/ownership").json()

    def update(self, changes, order_id=1, version=None):
        body = {"expected_version": self.state(order_id)["version"] if version is None else version,
                "actor": "测试操作人", "reason": "交接", **changes}
        return self.client.patch(f"/api/management/orders/{order_id}/ownership", json=body)

    def history(self, order_id=1):
        return self.client.get(f"/api/management/history?entity_type=order&order_id={order_id}").json()

    def batch(self, changes, ids=None, versions=None):
        ids = ids or [1, 2]
        return self.client.post("/api/management/orders/ownership/batch", json={"order_ids": ids,
            "expected_versions": versions if versions is not None else {str(value): self.state(value)["version"] for value in ids},
            "changes": changes, "actor": "测试操作人", "reason": "批量分配"})

    def test_single_assignment_auto_group_history_and_noop(self):
        response = self.update({"employee_id": self.employee["id"], "extra_fields": {"活动": "秋季"}})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["group_id"], self.group["id"])
        self.assertEqual(response.json()["sales_owner"], "张三")
        history = self.history()
        self.assertEqual(history["total"], 1)
        self.assertEqual(history["history"][0]["before"]["version"], 0)
        self.assertEqual(history["history"][0]["after"]["version"], 1)
        self.assertEqual(history["history"][0]["actor"], "测试操作人")
        self.assertEqual(history["history"][0]["reason"], "交接")
        self.assertEqual(self.update({"employee_id": self.employee["id"]}).json()["changed"], False)
        self.assertEqual(self.history()["total"], 1)

    def test_stale_single_update_rejected(self):
        self.update({"employee_id": self.employee["id"]})
        result = self.update({"employee_id": None, "group_id": None}, version=0)
        self.assertEqual(result.status_code, 409)
        self.assertEqual(self.state()["employee_id"], self.employee["id"])
        self.assertEqual(self.history()["total"], 1)

    def test_batch_atomic_and_common_batch_id(self):
        response = self.batch({"employee_id": self.employee["id"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["changed"], 2)
        self.assertEqual(self.history(1)["history"][0]["batch_id"], self.history(2)["history"][0]["batch_id"])
        for order_id in [1, 2]:
            self.assertEqual(self.state(order_id)["employee_id"], self.employee["id"])

    def test_batch_missing_order_rolls_back_first_order_and_history(self):
        response = self.batch({"employee_id": self.employee["id"]}, ids=[1, 999], versions={"1": 0, "999": 0})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.state()["version"], 0)
        self.assertEqual(self.history()["total"], 0)

    def test_batch_stale_order_rolls_back_other_order(self):
        self.update({"employee_id": self.employee["id"]}, order_id=2)
        response = self.batch({"group_id": self.group["id"]}, versions={"1": 0, "2": 0})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.state(1)["version"], 0)
        self.assertEqual(self.history(1)["total"], 0)
        self.assertEqual(self.history(2)["total"], 1)

    def test_batch_keeps_extra_and_supports_clear(self):
        self.update({"extra_fields": {"keep": "custom"}})
        self.batch({"employee_id": self.employee["id"]})
        self.assertEqual(self.state()["extra_fields"], {"keep": "custom"})
        response = self.batch({"employee_id": None, "group_id": None})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.state()["employee_id"])
        self.assertIsNone(self.state()["business_group"])
        self.assertEqual(self.state()["extra_fields"], {"keep": "custom"})

    def test_invalid_membership_and_inactive_entities(self):
        other = self.client.post("/api/management/groups", json={"code": "G2", "name": "二组"}).json()
        self.assertEqual(self.update({"employee_id": self.employee["id"], "group_id": other["id"]}).status_code, 422)
        result = self.client.put(f'/api/management/employees/{self.employee["id"]}', json={"code": "E1", "name": "张三", "group_id": self.group["id"], "active": False})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.update({"employee_id": self.employee["id"]}).status_code, 422)
        self.assertEqual(self.update({"group_id": 999}).status_code, 422)

    def test_group_deactivation_requires_employee_move_and_records_membership_history(self):
        response = self.client.put(f'/api/management/groups/{self.group["id"]}', json={"code": "G1", "name": "一组", "active": False})
        self.assertEqual(response.status_code, 409)
        self.client.put(f'/api/management/employees/{self.employee["id"]}', json={"code": "E1", "name": "张三", "group_id": None})
        response = self.client.put(f'/api/management/groups/{self.group["id"]}', json={"code": "G1", "name": "一组", "active": False})
        self.assertEqual(response.status_code, 200)
        history = self.client.get(f'/api/management/history?entity_type=employee&entity_id={self.employee["id"]}').json()
        self.assertEqual(history["total"], 2)
        self.assertEqual(history["history"][0]["before"]["group_id"], self.group["id"])
        self.assertIsNone(history["history"][0]["after"]["group_id"])

    def test_shop_defaults_are_scoped_and_do_not_rewrite_orders(self):
        body = {"employee_id": self.employee["id"], "expected_version": 0, "actor": "测试操作人"}
        response = self.client.patch("/api/management/shops/amazon/1/ownership", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(self.state()["sales_owner"])
        self.assertEqual(self.state()["shop_defaults"]["sales_owner"], "张三")
        shops = self.client.get("/api/management/shops").json()
        self.assertEqual(next(shop for shop in shops if shop["platform"] == "ebay")["version"], 0)
        self.assertEqual(self.client.patch("/api/management/shops/amazon/1/ownership", json=body).status_code, 409)
        history = self.client.get("/api/management/history?entity_type=shop&platform=amazon&shop_id=1").json()
        self.assertEqual(history["total"], 1)

    def test_repeat_sync_preserves_typed_ownership_history_and_source_json(self):
        self.update({"employee_id": self.employee["id"], "extra_fields": {"keep": "local"}})
        shop_body = {"employee_id": self.employee["id"], "expected_version": 0, "actor": "测试操作人"}
        self.client.patch("/api/management/shops/amazon/1/ownership", json=shop_body)
        before, history = self.state(), self.history()
        for _ in range(2):
            with patch.object(ingestion, "call", side_effect=[{"orders": [source_order(1, new_source_field=9)]}, {"orders": []}]):
                self.assertEqual(self.client.post("/api/base/sync/orders").status_code, 200)
            with patch.object(ingestion, "call", return_value={"sources": {"amazon": {"1": "New shop"}, "ebay": {"1": "B"}}}):
                self.assertEqual(self.client.post("/api/base/sync/shops").status_code, 200)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.history(), history)
        raw = self.client.get("/api/base/orders/1").json()["source_order"]
        self.assertEqual(raw["original_future_field"], {"preserve": True})
        self.assertEqual(raw["new_source_field"], 9)

    def test_employee_rename_move_does_not_rewrite_order_snapshot(self):
        self.update({"employee_id": self.employee["id"]})
        self.client.put(f'/api/management/employees/{self.employee["id"]}', json={"code": "E1", "name": "新名字", "group_id": None})
        self.assertEqual(self.state()["sales_owner"], "张三")
        self.assertEqual(self.state()["business_group"], "一组")
        self.assertEqual(self.history()["total"], 1)

    def test_legacy_text_api_is_audited_and_clears_old_id(self):
        self.update({"employee_id": self.employee["id"]})
        response = self.client.patch("/api/base/orders/1/annotation", json={"sales_owner": "旧文本员工"})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.state()["employee_id"])
        self.assertEqual(self.state()["sales_owner"], "旧文本员工")
        self.assertEqual(self.history()["history"][0]["actor"], "兼容接口")

    def test_filters_pagination_and_history_pagination(self):
        self.batch({"employee_id": self.employee["id"]})
        result = self.client.get(f'/api/management/orders?group_id={self.group["id"]}&limit=1&offset=1').json()
        self.assertEqual(result["total"], 2)
        self.assertEqual(len(result["orders"]), 1)
        self.assertEqual(self.client.get('/api/management/orders?unassigned=true').json()["total"], 1)
        self.update({"extra_fields": {"change": 1}})
        data = self.client.get('/api/management/history?entity_type=order&order_id=1&limit=1&offset=1').json()
        self.assertEqual(data["total"], 2)
        self.assertEqual(data["history"][0]["before"]["version"], 0)

    def test_validation_empty_changes_duplicate_codes_and_bad_batches(self):
        self.assertEqual(self.client.post('/api/management/groups', json={"code": "g1", "name": "重复"}).status_code, 409)
        self.assertEqual(self.client.post('/api/management/employees', json={"code": "E2", "name": "   "}).status_code, 422)
        for changes in [{}, {"extra_fields": None}, {"unexpected": 1}]:
            self.assertEqual(self.update(changes).status_code, 422)
        self.assertEqual(self.batch({"employee_id": self.employee["id"]}, ids=[1, 1], versions={"1": 0}).status_code, 422)
        self.assertEqual(self.batch({"employee_id": self.employee["id"]}, versions={"1": 0}).status_code, 422)
        self.assertEqual(self.update({"extra_fields": {"large": "x" * 65536}}).status_code, 422)
        self.assertEqual(self.history()["total"], 0)

    def test_legacy_migration_is_additive_and_repeatable(self):
        path = Path(self.tmp.name) / 'legacy.db'
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE order_annotations(order_id INTEGER PRIMARY KEY,sales_owner TEXT,business_group TEXT,extra_json TEXT NOT NULL,updated_at TEXT NOT NULL)")
            db.execute("INSERT INTO order_annotations VALUES(1,'原员工','原小组','{\"keep\": 1}','2026-01-01')")
        with patch.object(main, 'DB_PATH', path):
            main.initialize(); main.initialize()
            with main.database() as db:
                row = dict(db.execute('SELECT * FROM order_annotations').fetchone())
                self.assertEqual(row['sales_owner'], '原员工')
                self.assertEqual(row['business_group'], '原小组')
                self.assertEqual(json.loads(row['extra_json']), {"keep": 1})
                self.assertEqual(row['version'], 0)
                self.assertIsNone(row['employee_id'])
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')


if __name__ == '__main__':
    unittest.main()
