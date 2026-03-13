#!/usr/bin/env python3
"""
test_alert_manager.py — 告警管理模組單元測試

測試項目：
1. is_duplicate() 記憶體快取去重
2. is_duplicate() SQLite 持久化去重（跨重啟）
3. call_with_escalation() 成功撥號即停止
4. call_with_escalation() 未接重撥邏輯
5. call_with_escalation() 升級到下一個目標
6. _log_call() SQLite 寫入與 call_log 表結構驗證
"""

import os
import sys
import time
import sqlite3
import tempfile
import unittest
from unittest.mock import patch, MagicMock, call

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 模擬 pjsua2，因為可能未安裝
sys.modules["pjsua2"] = MagicMock()

# 模擬 sip_caller 模組
from unittest.mock import MagicMock
mock_sip = MagicMock()
sys.modules["sip_caller"] = mock_sip

from sip_caller import CallResult, CallReport


class TestAlertManagerDedup(unittest.TestCase):
    """去重邏輯測試"""

    def _make_manager(self, tmpdir):
        """建立用於測試的 AlertManager"""
        from alert_manager import AlertManager
        config = {
            "alert": {
                "dedup_window_seconds": 60,
                "max_retry": 2,
                "retry_interval_seconds": 1,
                "escalation": True,
            },
            "logging": {
                "db_path": os.path.join(tmpdir, "test_alerts.db")
            },
            "sip": {
                "server": "test.server",
                "port": 5061,
                "transport": "tls",
                "username": "test",
                "password": "test",
            }
        }
        return AlertManager(config)

    def test_first_alert_not_duplicate(self):
        """第一次出現的告警不應是重複"""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = self._make_manager(tmpdir)
            self.assertFalse(mgr.is_duplicate("alert_vm01_cpu"))

    def test_same_alert_in_window_is_duplicate(self):
        """窗口內相同告警應被判為重複"""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = self._make_manager(tmpdir)
            mgr.is_duplicate("alert_vm01_cpu")  # 第一次
            self.assertTrue(mgr.is_duplicate("alert_vm01_cpu"))  # 第二次

    def test_different_alerts_not_duplicate(self):
        """不同告警不應相互影響"""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = self._make_manager(tmpdir)
            mgr.is_duplicate("alert_vm01_cpu")
            self.assertFalse(mgr.is_duplicate("alert_vm02_ram"))

    def test_sqlite_persistence_dedup(self):
        """SQLite 持久化去重：重啟後應讀取 DB 去重"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_alerts.db")

            # 第一個 manager 寫入成功記錄
            mgr1 = self._make_manager(tmpdir)
            mgr1.is_duplicate("alert_vm01_cpu")

            # 手動在 DB 插入一筆成功記錄
            conn = sqlite3.connect(db_path)
            conn.execute(
                "INSERT INTO call_log "
                "(timestamp, alert_key, alert_name, resource_name, "
                "criticality, target_name, target_number, attempt, "
                "result, duration_seconds, error_message, routed_group) "
                "VALUES (datetime('now'), ?, '', '', '', 'test', '1001', 1, "
                "'success', 5.0, '', '')",
                ("alert_vm01_cpu",)
            )
            conn.commit()
            conn.close()

            # 第二個 manager（模擬重啟），應讀 SQLite 判為重複
            mgr2 = self._make_manager(tmpdir)
            self.assertTrue(mgr2.is_duplicate("alert_vm01_cpu"))


class TestAlertManagerCallLogSchema(unittest.TestCase):
    """call_log 表結構驗證（必須與 Agent C 一致）"""

    EXPECTED_COLUMNS = {
        "id", "timestamp", "alert_key", "alert_name", "resource_name",
        "criticality", "target_name", "target_number", "attempt",
        "result", "duration_seconds", "error_message", "routed_group"
    }

    def test_call_log_table_has_correct_columns(self):
        """call_log 表必須包含全部 13 個欄位（含 id）"""
        with tempfile.TemporaryDirectory() as tmpdir:
            from alert_manager import AlertManager
            config = {
                "alert": {
                    "dedup_window_seconds": 300,
                    "max_retry": 3,
                    "retry_interval_seconds": 120,
                    "escalation": True,
                },
                "logging": {
                    "db_path": os.path.join(tmpdir, "test.db")
                },
                "sip": {}
            }
            mgr = AlertManager(config)

            conn = sqlite3.connect(mgr.db_path)
            cursor = conn.execute("PRAGMA table_info(call_log)")
            columns = {row[1] for row in cursor.fetchall()}
            conn.close()

            self.assertEqual(
                columns,
                self.EXPECTED_COLUMNS,
                f"欄位不符合介面契約。\n期望: {self.EXPECTED_COLUMNS}\n實際: {columns}"
            )

    def test_routed_group_has_default_empty(self):
        """routed_group 欄位應有 DEFAULT ''"""
        with tempfile.TemporaryDirectory() as tmpdir:
            from alert_manager import AlertManager
            config = {
                "alert": {
                    "dedup_window_seconds": 300,
                    "max_retry": 3,
                    "retry_interval_seconds": 120,
                    "escalation": True,
                },
                "logging": {
                    "db_path": os.path.join(tmpdir, "test.db")
                },
                "sip": {}
            }
            mgr = AlertManager(config)

            conn = sqlite3.connect(mgr.db_path)
            # 插入不帶 routed_group 的記錄
            conn.execute(
                "INSERT INTO call_log "
                "(timestamp, alert_key, target_name, target_number, attempt, result) "
                "VALUES (datetime('now'), 'key1', 'test', '1001', 1, 'success')"
            )
            conn.commit()
            row = conn.execute("SELECT routed_group FROM call_log").fetchone()
            conn.close()

            self.assertEqual(row[0], "", "routed_group 預設值應為空字串")


class TestCallWithEscalation(unittest.TestCase):
    """call_with_escalation() 撥號邏輯測試"""

    def _make_manager(self, tmpdir):
        from alert_manager import AlertManager
        config = {
            "alert": {
                "dedup_window_seconds": 300,
                "max_retry": 2,
                "retry_interval_seconds": 0,  # 測試用，不等待
                "escalation": True,
            },
            "logging": {
                "db_path": os.path.join(tmpdir, "test.db")
            },
            "sip": {
                "server": "test.server",
                "port": 5061,
                "transport": "tls",
                "username": "test",
                "password": "test",
            }
        }
        return AlertManager(config)

    def test_stop_on_success(self):
        """第一次撥號成功時應立刻停止，不繼續撥其他目標"""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = self._make_manager(tmpdir)

            success_report = CallReport(
                result=CallResult.SUCCESS,
                target="1001",
                duration_seconds=5.0
            )

            with patch("alert_manager.make_sip_call", return_value=success_report) as mock_call:
                mgr.call_with_escalation(
                    wav_path="/tmp/test.wav",
                    targets=[
                        {"name": "Alice", "number": "1001", "priority": 1},
                        {"name": "Bob", "number": "1002", "priority": 2},
                    ],
                    alert_data={"alertName": "CPU_High", "resourceName": "vm01"}
                )

            # 只應撥一次
            mock_call.assert_called_once()

    def test_retry_on_no_answer(self):
        """未接聽時應重撥，達到 max_retry 次"""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = self._make_manager(tmpdir)

            no_answer_report = CallReport(
                result=CallResult.NO_ANSWER,
                target="1001",
                duration_seconds=30.0
            )

            with patch("alert_manager.make_sip_call", return_value=no_answer_report) as mock_call:
                mgr.call_with_escalation(
                    wav_path="/tmp/test.wav",
                    targets=[
                        {"name": "Alice", "number": "1001", "priority": 1},
                    ],
                    alert_data={"alertName": "CPU_High", "resourceName": "vm01"}
                )

            # 應撥 max_retry=2 次
            self.assertEqual(mock_call.call_count, 2)

    def test_escalation_to_next_target(self):
        """所有重撥失敗後應升級到下一個目標"""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = self._make_manager(tmpdir)

            no_answer = CallReport(
                result=CallResult.NO_ANSWER,
                target="1001",
                duration_seconds=30.0
            )
            success = CallReport(
                result=CallResult.SUCCESS,
                target="1002",
                duration_seconds=5.0
            )

            # Alice 未接（2次），Bob 接聽
            with patch("alert_manager.make_sip_call", side_effect=[no_answer, no_answer, success]) as mock_call:
                mgr.call_with_escalation(
                    wav_path="/tmp/test.wav",
                    targets=[
                        {"name": "Alice", "number": "1001", "priority": 1},
                        {"name": "Bob", "number": "1002", "priority": 2},
                    ],
                    alert_data={"alertName": "CPU_High", "resourceName": "vm01"}
                )

            # Alice 2次 + Bob 1次 = 3次
            self.assertEqual(mock_call.call_count, 3)

    def test_routed_group_logged(self):
        """routed_group 應被正確記錄到 DB"""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = self._make_manager(tmpdir)

            success_report = CallReport(
                result=CallResult.SUCCESS,
                target="1001",
                duration_seconds=5.0
            )

            with patch("alert_manager.make_sip_call", return_value=success_report):
                mgr.call_with_escalation(
                    wav_path="/tmp/test.wav",
                    targets=[{"name": "Alice", "number": "1001", "priority": 1}],
                    alert_data={"alertName": "CPU_High", "resourceName": "vm01"},
                    routed_group="客戶A維運群組"
                )

            conn = sqlite3.connect(mgr.db_path)
            row = conn.execute("SELECT routed_group FROM call_log").fetchone()
            conn.close()

            self.assertEqual(row[0], "客戶A維運群組")


if __name__ == "__main__":
    unittest.main(verbosity=2)
