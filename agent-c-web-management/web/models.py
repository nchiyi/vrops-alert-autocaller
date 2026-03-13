#!/usr/bin/env python3
"""
models.py — 資料庫模型
管理聯絡人、群組、路由規則、通話紀錄的 CRUD。
"""

import sqlite3
import logging
from datetime import datetime
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

DB_PATH = "/opt/vrops-alert-caller/logs/alerts.db"


def get_db():
    """取得資料庫連線"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """初始化所有資料表"""
    conn = get_db()

    # 聯絡人群組
    conn.execute("""
        CREATE TABLE IF NOT EXISTS contact_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    # 聯絡人
    conn.execute("""
        CREATE TABLE IF NOT EXISTS contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            number TEXT NOT NULL,
            group_id INTEGER,
            priority INTEGER DEFAULT 1,
            enabled INTEGER DEFAULT 1,
            note TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (group_id) REFERENCES contact_groups(id)
                ON DELETE SET NULL
        )
    """)

    # 告警路由規則
    conn.execute("""
        CREATE TABLE IF NOT EXISTS routing_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            match_field TEXT NOT NULL DEFAULT 'resourceName',
            match_pattern TEXT NOT NULL,
            target_group_id INTEGER NOT NULL,
            priority INTEGER DEFAULT 1,
            enabled INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (target_group_id) REFERENCES contact_groups(id)
                ON DELETE CASCADE
        )
    """)

    # 通話紀錄（與 Agent B alert_manager.py 欄位完全一致，共 12 欄）
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

    # 索引
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_call_log_ts "
        "ON call_log(timestamp DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_call_log_alert "
        "ON call_log(alert_key)"
    )

    # 插入預設群組
    conn.execute(
        "INSERT OR IGNORE INTO contact_groups(name, description) "
        "VALUES ('預設群組', '未匹配任何路由規則時的撥號目標')"
    )

    conn.commit()
    conn.close()
    logger.info("資料庫初始化完成")


# ============================
# 聯絡人 CRUD
# ============================

def contact_list(group_id: Optional[int] = None) -> List[Dict]:
    conn = get_db()
    if group_id:
        rows = conn.execute(
            "SELECT c.*, g.name as group_name "
            "FROM contacts c LEFT JOIN contact_groups g ON c.group_id=g.id "
            "WHERE c.group_id=? ORDER BY c.priority",
            (group_id,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT c.*, g.name as group_name "
            "FROM contacts c LEFT JOIN contact_groups g ON c.group_id=g.id "
            "ORDER BY c.group_id, c.priority"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def contact_create(name: str, number: str, group_id: int,
                   priority: int = 1, note: str = "") -> int:
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO contacts(name, number, group_id, priority, note) "
        "VALUES (?, ?, ?, ?, ?)",
        (name, number, group_id, priority, note)
    )
    conn.commit()
    contact_id = cur.lastrowid
    conn.close()
    return contact_id


def contact_update(contact_id: int, **fields) -> bool:
    conn = get_db()
    allowed = {"name", "number", "group_id", "priority", "enabled", "note"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False
    updates["updated_at"] = datetime.now().isoformat()
    set_clause = ", ".join(f"{k}=?" for k in updates)
    conn.execute(
        f"UPDATE contacts SET {set_clause} WHERE id=?",
        (*updates.values(), contact_id)
    )
    conn.commit()
    conn.close()
    return True


def contact_delete(contact_id: int) -> bool:
    conn = get_db()
    conn.execute("DELETE FROM contacts WHERE id=?", (contact_id,))
    conn.commit()
    conn.close()
    return True


# ============================
# 群組 CRUD
# ============================

def group_list() -> List[Dict]:
    conn = get_db()
    rows = conn.execute(
        "SELECT g.*, COUNT(c.id) as member_count "
        "FROM contact_groups g "
        "LEFT JOIN contacts c ON c.group_id=g.id "
        "GROUP BY g.id ORDER BY g.name"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def group_create(name: str, description: str = "") -> int:
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO contact_groups(name, description) VALUES (?, ?)",
        (name, description)
    )
    conn.commit()
    gid = cur.lastrowid
    conn.close()
    return gid


def group_delete(group_id: int) -> bool:
    conn = get_db()
    conn.execute("DELETE FROM contact_groups WHERE id=?", (group_id,))
    conn.commit()
    conn.close()
    return True


# ============================
# 路由規則 CRUD
# ============================

def rule_list() -> List[Dict]:
    conn = get_db()
    rows = conn.execute(
        "SELECT r.*, g.name as group_name "
        "FROM routing_rules r "
        "JOIN contact_groups g ON r.target_group_id=g.id "
        "ORDER BY r.priority"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def rule_create(name: str, match_field: str, match_pattern: str,
                target_group_id: int, priority: int = 1,
                description: str = "") -> int:
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO routing_rules"
        "(name, match_field, match_pattern, target_group_id, priority, description) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (name, match_field, match_pattern, target_group_id, priority, description)
    )
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    return rid


def rule_delete(rule_id: int) -> bool:
    conn = get_db()
    conn.execute("DELETE FROM routing_rules WHERE id=?", (rule_id,))
    conn.commit()
    conn.close()
    return True


# ============================
# 通話紀錄查詢
# ============================

def call_log_query(
    limit: int = 50,
    offset: int = 0,
    alert_name: str = "",
    resource_name: str = "",
    result: str = "",
    date_from: str = "",
    date_to: str = ""
) -> Dict:
    """搜尋通話紀錄（含分頁和篩選）"""
    conn = get_db()
    conditions = []
    params = []

    if alert_name:
        conditions.append("alert_name LIKE ?")
        params.append(f"%{alert_name}%")
    if resource_name:
        conditions.append("resource_name LIKE ?")
        params.append(f"%{resource_name}%")
    if result:
        conditions.append("result = ?")
        params.append(result)
    if date_from:
        conditions.append("timestamp >= ?")
        params.append(date_from)
    if date_to:
        conditions.append("timestamp <= ?")
        params.append(date_to)

    where = "WHERE " + " AND ".join(conditions) if conditions else ""

    # 總數
    total = conn.execute(
        f"SELECT COUNT(*) FROM call_log {where}", params
    ).fetchone()[0]

    # 資料
    rows = conn.execute(
        f"SELECT * FROM call_log {where} "
        f"ORDER BY id DESC LIMIT ? OFFSET ?",
        params + [limit, offset]
    ).fetchall()

    conn.close()
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "records": [dict(r) for r in rows]
    }
