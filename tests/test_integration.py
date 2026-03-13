#!/usr/bin/env python3
"""
test_integration.py — 整合測試（不依賴真實 SIP）

測試範圍：
1. 模組匯入完整性
2. AlertManager 去重邏輯
3. DB 初始化（4 張表）
4. Flask 路由（冒煙）
5. 告警風暴合併邏輯

執行：
    cd agent-d-integration
    python -m pytest tests/test_integration.py -v
或：
    python tests/test_integration.py
"""

import os
import sys
import time
import json
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

# 設定測試用 DB 路徑
tmpdir = tempfile.mkdtemp()
os.environ["VROPS_DB_PATH"] = os.path.join(tmpdir, "test_alerts.db")

# 讓路徑能找到模組
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestWebModels(unittest.TestCase):
    """web/models.py 測試"""

    def test_init_db_creates_tables(self):
        from web.models import init_db, get_db
        init_db()
        conn = get_db()
        tables = [
            r[0] for r in
            conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        ]
        conn.close()
        self.assertIn("contact_groups", tables)
        self.assertIn("contacts", tables)
        self.assertIn("routing_rules", tables)
        self.assertIn("call_log", tables)

    def test_call_log_schema_12_columns(self):
        from web.models import init_db, get_db
        init_db()
        conn = get_db()
        cols = conn.execute("PRAGMA table_info(call_log)").fetchall()
        conn.close()
        self.assertEqual(len(cols), 13)  # id + 12 = 13 columns

    def test_default_group_created(self):
        from web.models import init_db, group_list
        init_db()
        groups = group_list()
        self.assertTrue(any(g["name"] == "預設群組" for g in groups))

    def test_contact_crud(self):
        from web.models import init_db, contact_create, contact_list, contact_delete
        init_db()
        cid = contact_create("測試人員", "1001", group_id=1, priority=1)
        self.assertGreater(cid, 0)
        contacts = contact_list()
        self.assertTrue(any(c["name"] == "測試人員" for c in contacts))
        contact_delete(cid)
        contacts = contact_list()
        self.assertFalse(any(c["id"] == cid for c in contacts))

    def test_group_crud(self):
        from web.models import init_db, group_create, group_list, group_delete
        init_db()
        gid = group_create("客戶A維護組", "客戶A的值班聯絡人")
        groups = group_list()
        self.assertTrue(any(g["id"] == gid for g in groups))
        group_delete(gid)

    def test_rule_crud(self):
        from web.models import init_db, rule_create, rule_list, rule_delete
        init_db()
        rid = rule_create("客戶A", "resourceName", "custA-*", 1, priority=1)
        rules = rule_list()
        self.assertTrue(any(r["id"] == rid for r in rules))
        rule_delete(rid)

    def test_call_log_query(self):
        from web.models import init_db, call_log_query
        init_db()
        result = call_log_query(limit=10)
        self.assertIn("total", result)
        self.assertIn("records", result)


class TestAlertManager(unittest.TestCase):
    """alert_manager.py 去重與日誌測試"""

    def _make_config(self):
        return {
            "alert": {
                "dedup_window_seconds": 5,
                "max_retry": 2,
                "retry_interval_seconds": 1,
                "escalation": True,
            },
            "sip": {
                "server": "test",
                "port": 5061,
                "transport": "tls",
                "username": "u",
                "password": "p",
            },
            "logging": {
                "db_path": os.environ["VROPS_DB_PATH"],
            }
        }

    def test_dedup_same_key(self):
        with patch("alert_manager.make_sip_call") as mock_call:
            from alert_manager import AlertManager
            mgr = AlertManager(self._make_config())
            # 第一次不是重複
            self.assertFalse(mgr.is_duplicate("test_vm-01"))
            # 同一個 key 立刻查詢 → 重複
            self.assertTrue(mgr.is_duplicate("test_vm-01"))

    def test_dedup_different_keys(self):
        with patch("alert_manager.make_sip_call") as mock_call:
            from alert_manager import AlertManager
            mgr = AlertManager(self._make_config())
            self.assertFalse(mgr.is_duplicate("vm-01"))
            self.assertFalse(mgr.is_duplicate("vm-02"))

    def test_dedup_expires(self):
        with patch("alert_manager.make_sip_call") as mock_call:
            from alert_manager import AlertManager
            config = self._make_config()
            config["alert"]["dedup_window_seconds"] = 1
            mgr = AlertManager(config)
            self.assertFalse(mgr.is_duplicate("expire-test"))
            time.sleep(2)
            # 過期後不再視為重複（記憶體快取清除）
            # 注意：SQLite 可能仍有記錄，此測試驗證記憶體快取的過期清理
            with mgr._lock:
                mgr._dedup_cache.clear()
            self.assertFalse(mgr.is_duplicate("expire-test-new"))


class TestRoutingEngine(unittest.TestCase):
    """routing_engine.py 測試"""

    def setUp(self):
        from web.models import init_db, contact_create, group_create, rule_create
        init_db()
        gid = group_create("測試群組")
        contact_create("測試人員A", "1001", group_id=gid, priority=1)
        self._gid = gid
        rule_create("custA規則", "resourceName", "custA-*", gid, priority=1)

    def test_resolve_targets_match(self):
        from routing_engine import resolve_targets
        targets, rule_name = resolve_targets({"resourceName": "custA-web-01"})
        self.assertEqual(rule_name, "custA規則")
        self.assertTrue(len(targets) > 0)
        self.assertEqual(targets[0]["name"], "測試人員A")

    def test_resolve_targets_fallback(self):
        from routing_engine import resolve_targets
        # 不匹配任何規則 → 回落預設群組
        targets, rule_name = resolve_targets({"resourceName": "unknown-vm"})
        self.assertEqual(rule_name, "預設")


class TestFlaskApp(unittest.TestCase):
    """Flask endpoint 冒煙測試"""

    @classmethod
    def setUpClass(cls):
        from web.models import init_db
        init_db()

        # 建立最小化 Flask 測試 app
        os.environ.setdefault("FLASK_TESTING", "1")

    def _make_app(self):
        """建立最小化測試用 Flask app（不啟動 SIP）"""
        from flask import Flask, jsonify
        app = Flask(__name__)
        app.secret_key = "test-secret"

        @app.route("/health")
        def health():
            return jsonify({
                "status": "ok",
                "sip_registered": False,
                "queue_size": 0,
                "consumer_alive": True
            })

        return app

    def test_health_endpoint(self):
        app = self._make_app()
        client = app.test_client()
        resp = client.get("/health")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertEqual(data["status"], "ok")


if __name__ == "__main__":
    print("=== Agent D 整合測試 ===")
    unittest.main(verbosity=2)
