#!/usr/bin/env python3
"""
routing_engine.py — 告警路由引擎
根據告警內容匹配規則，決定通知哪組人。
"""

import fnmatch
import logging
from typing import List, Dict, Tuple

from web.models import get_db

logger = logging.getLogger(__name__)


def resolve_targets(alert_data: dict) -> Tuple[List[Dict], str]:
    """
    根據告警資料匹配路由規則，回傳應通知的聯絡人列表。

    匹配邏輯：
    1. 按 priority 排序讀取所有啟用的規則
    2. 對每條規則，用 fnmatch 比對 alert_data[match_field]
    3. 第一條匹配的規則 → 取出該群組的所有聯絡人
    4. 無匹配 → 使用「預設群組」(id=1)
    5. 群組為空 → 回退到預設群組 (id=1)

    Args:
        alert_data: vROps 推送的 JSON 告警資料

    Returns:
        Tuple[List[Dict], str]:
            - 聯絡人列表 [{"name":..., "number":..., "priority":...}]
            - 匹配的規則名稱
    """
    conn = get_db()

    # 讀取所有啟用的路由規則，按 priority 排序
    rules = conn.execute(
        "SELECT * FROM routing_rules WHERE enabled=1 ORDER BY priority"
    ).fetchall()

    matched_group_id = None
    matched_rule_name = None

    for rule in rules:
        field = rule["match_field"]       # e.g. "resourceName"
        pattern = rule["match_pattern"]   # e.g. "custA-*"
        value = str(alert_data.get(field, ""))  # 告警中該欄位的值

        if fnmatch.fnmatch(value, pattern):
            matched_group_id = rule["target_group_id"]
            matched_rule_name = rule["name"]
            logger.info(
                f"路由匹配: 規則'{matched_rule_name}' "
                f"({field}='{value}' ~ '{pattern}') "
                f"→ 群組 ID={matched_group_id}"
            )
            break

    # 無匹配 → 預設群組
    if matched_group_id is None:
        matched_group_id = 1
        matched_rule_name = "預設"
        logger.info("無路由匹配，使用預設群組")

    # 取出群組中所有啟用的聯絡人
    contacts = conn.execute(
        "SELECT name, number, priority FROM contacts "
        "WHERE group_id=? AND enabled=1 ORDER BY priority",
        (matched_group_id,)
    ).fetchall()

    conn.close()

    # 空群組回退機制：若指定群組無聯絡人，回退到預設群組 (id=1)
    if not contacts and matched_group_id != 1:
        logger.warning(
            f"群組 {matched_group_id} 沒有啟用的聯絡人，回退到預設群組"
        )
        conn = get_db()
        contacts = conn.execute(
            "SELECT name, number, priority FROM contacts "
            "WHERE group_id=1 AND enabled=1 ORDER BY priority"
        ).fetchall()
        conn.close()

    result = [dict(c) for c in contacts]
    logger.info(
        f"路由結果: 規則='{matched_rule_name}', "
        f"聯絡人={[c['name'] for c in result]}"
    )
    return result, matched_rule_name
