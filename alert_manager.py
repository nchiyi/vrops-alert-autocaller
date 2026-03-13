#!/usr/bin/env python3
"""
alert_manager.py — 告警管理模組
處理去重、重撥、升級、日誌等告警生命週期管理。
"""

import time
import logging
import sqlite3
import threading
from datetime import datetime
from typing import List, Dict, Optional
from collections import OrderedDict

from sip_caller import make_sip_call, CallResult, CallReport

logger = logging.getLogger(__name__)


class AlertManager:
    """告警管理器"""

    def __init__(self, config: dict):
        self.config = config
        alert_cfg = config.get("alert", {})

        # 去重參數
        self.dedup_window = alert_cfg.get("dedup_window_seconds", 300)
        self.max_retry = alert_cfg.get("max_retry", 3)
        self.retry_interval = alert_cfg.get("retry_interval_seconds", 120)
        self.escalation_enabled = alert_cfg.get("escalation", True)

        # 去重快取 (key → timestamp)
        self._dedup_cache: OrderedDict = OrderedDict()
        self._lock = threading.Lock()

        # 初始化 SQLite 日誌
        self._init_db()

    # ============================
    # 告警去重
    # ============================

    def is_duplicate(self, alert_key: str) -> bool:
        """
        檢查是否為重複告警。
        同時使用記憶體快取（快）和 SQLite（持久化），
        確保服務重啟後仍能正確去重。
        """
        now = time.time()

        with self._lock:
            # 清理過期的記憶體快取
            expired = [
                k for k, t in self._dedup_cache.items()
                if now - t > self.dedup_window
            ]
            for k in expired:
                del self._dedup_cache[k]

            # 1. 先查記憶體快取
            if alert_key in self._dedup_cache:
                return True

            # 2. 再查 SQLite（服務重啟後的持久化去重）
            try:
                conn = sqlite3.connect(self.db_path)
                row = conn.execute(
                    "SELECT 1 FROM call_log "
                    "WHERE alert_key = ? "
                    "AND timestamp > datetime('now', ?)",
                    (alert_key, f'-{self.dedup_window} seconds')
                ).fetchone()
                conn.close()

                if row:
                    self._dedup_cache[alert_key] = now
                    return True
            except Exception as e:
                logger.warning(f"SQLite 去重查詢失敗: {e}")

            # 3. 新告警，記入快取
            self._dedup_cache[alert_key] = now
            return False

    # ============================
    # 撥號邏輯（含重撥與升級）
    # ============================

    def call_with_escalation(
        self,
        wav_path: str,
        targets: List[Dict],
        alert_data: dict,
        routed_group: str = ""
    ):
        """
        依優先序撥號，未接則重撥，重撥失敗則升級到下一個目標。

        流程：
        1. 撥給 priority=1 的人
        2. 未接 → 等待 → 重撥（最多 max_retry 次）
        3. 仍未接 → 撥給 priority=2 的人
        4. 依此類推
        """
        sip_config = self.config["sip"]
        alert_key = (
            f"{alert_data.get('alertName', 'unknown')}_"
            f"{alert_data.get('resourceName', 'unknown')}"
        )

        for target in targets:
            target_name = target["name"]
            target_number = target["number"]

            logger.info(
                f"撥號目標: {target_name} ({target_number}) "
                f"[priority={target['priority']}]"
            )

            for attempt in range(1, self.max_retry + 1):
                logger.info(
                    f"撥號嘗試 {attempt}/{self.max_retry}: "
                    f"{target_name} ({target_number})"
                )

                report = make_sip_call(
                    wav_path=wav_path,
                    target_number=target_number,
                    config=sip_config
                )

                self._log_call(
                    alert_key=alert_key,
                    target_name=target_name,
                    target_number=target_number,
                    attempt=attempt,
                    report=report,
                    alert_data=alert_data,
                    routed_group=routed_group
                )

                if report.result == CallResult.SUCCESS:
                    logger.info(
                        f"告警通知成功: {target_name} 已接聽 "
                        f"(耗時 {report.duration_seconds:.1f}s)"
                    )
                    return

                if attempt < self.max_retry:
                    logger.info(
                        f"{target_name} 未接聽，"
                        f"{self.retry_interval} 秒後重撥..."
                    )
                    time.sleep(self.retry_interval)

            logger.warning(
                f"{target_name} ({target_number}) "
                f"{self.max_retry} 次撥號均未接聽"
            )

            if not self.escalation_enabled:
                logger.warning("升級撥號未啟用，停止撥號")
                return

            logger.info("升級到下一個撥號目標...")

        logger.critical(
            f"所有撥號目標均未接聽！告警: {alert_key}"
        )

    # ============================
    # SQLite 日誌
    # ============================

    def _init_db(self):
        """初始化 SQLite 通話日誌資料庫"""
        db_path = self.config.get("logging", {}).get(
            "db_path",
            "/opt/vrops-alert-caller/logs/alerts.db"
        )

        self.db_path = db_path

        import os
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS call_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                alert_key TEXT NOT NULL,
                alert_name TEXT,
                resource_name TEXT,
                criticality TEXT,
                target_name TEXT NOT NULL,
                target_number TEXT NOT NULL,
                attempt INTEGER NOT NULL,
                result TEXT NOT NULL,
                duration_seconds REAL,
                error_message TEXT,
                routed_group TEXT DEFAULT ''
            )
        """)
        conn.commit()
        conn.close()

        logger.info(f"通話日誌資料庫: {db_path}")

    def _log_call(
        self,
        alert_key: str,
        target_name: str,
        target_number: str,
        attempt: int,
        report: CallReport,
        alert_data: dict,
        routed_group: str = ""
    ):
        """記錄一筆通話日誌"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                """
                INSERT INTO call_log
                (timestamp, alert_key, alert_name, resource_name,
                 criticality, target_name, target_number,
                 attempt, result, duration_seconds, error_message,
                 routed_group)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now().isoformat(),
                    alert_key,
                    alert_data.get("alertName", ""),
                    alert_data.get("resourceName", ""),
                    alert_data.get("criticality", ""),
                    target_name,
                    target_number,
                    attempt,
                    report.result.value,
                    report.duration_seconds,
                    report.error_message,
                    routed_group
                )
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"寫入通話日誌失敗: {e}")

    def get_history(self, limit: int = 50) -> List[Dict]:
        """查詢告警處理歷史（供 API 回傳）"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM call_log ORDER BY id DESC LIMIT ?",
                (limit,)
            ).fetchall()
            conn.close()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"查詢通話日誌失敗: {e}")
            return []
