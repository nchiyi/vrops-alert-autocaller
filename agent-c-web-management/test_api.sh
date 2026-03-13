#!/bin/bash
# Agent C — API 測試腳本
# 假設 webhook_server.py 已啟動於 localhost:5000
# 先以瀏覽器登入取得 session cookie，再測試 API

BASE="http://localhost:5000"
COOKIE="session=YOUR_SESSION_COOKIE"  # 替換為登入後的 session cookie

echo "=== 群組 API ==="
echo "--- 取得群組列表 ---"
curl -s -b "$COOKIE" "$BASE/api/groups" | python3 -m json.tool

echo "--- 新增群組 ---"
curl -s -b "$COOKIE" -X POST "$BASE/api/groups" \
  -H "Content-Type: application/json" \
  -d '{"name":"客戶A維護組","description":"負責客戶A的值班人員"}' | python3 -m json.tool

echo ""
echo "=== 聯絡人 API ==="
echo "--- 取得聯絡人列表 ---"
curl -s -b "$COOKIE" "$BASE/api/contacts" | python3 -m json.tool

echo "--- 新增聯絡人 ---"
curl -s -b "$COOKIE" -X POST "$BASE/api/contacts" \
  -H "Content-Type: application/json" \
  -d '{"name":"王大明","number":"0912345678","group_id":1,"priority":1}' | python3 -m json.tool

echo ""
echo "=== 路由規則 API ==="
echo "--- 取得路由規則列表 ---"
curl -s -b "$COOKIE" "$BASE/api/rules" | python3 -m json.tool

echo "--- 新增路由規則 ---"
curl -s -b "$COOKIE" -X POST "$BASE/api/rules" \
  -H "Content-Type: application/json" \
  -d '{"name":"客戶A告警","match_field":"resourceName","match_pattern":"custA-*","target_group_id":2,"priority":1}' | python3 -m json.tool

echo ""
echo "=== 通話紀錄 API ==="
echo "--- 取得通話紀錄（最近10筆）---"
curl -s -b "$COOKIE" "$BASE/api/call-history?limit=10" | python3 -m json.tool

echo "--- 篩選特定資源的通話 ---"
curl -s -b "$COOKIE" "$BASE/api/call-history?resource_name=custA&result=answered" | python3 -m json.tool

echo ""
echo "=== 完成 ==="
