#!/usr/bin/env bash
# =============================================================
# 冒煙測試腳本 — Agent D 整合驗證
# 執行前確保 webhook_server.py 已啟動（port 5000）
# =============================================================

BASE_URL="http://localhost:5000"
PASS=0
FAIL=0

check() {
    local name="$1"
    local result="$2"
    local expected="$3"
    if echo "$result" | grep -q "$expected"; then
        echo "PASS: $name"
        PASS=$((PASS+1))
    else
        echo "FAIL: $name"
        echo "  Expected: $expected"
        echo "  Got: $result"
        FAIL=$((FAIL+1))
    fi
}

echo "=== vROps Alert AutoCaller — 冒煙測試 ==="
echo ""

# 1. 健康檢查
echo "[1] 健康檢查 /health"
result=$(curl -s "$BASE_URL/health")
check "health status ok" "$result" '"status": "ok"'
check "consumer_alive" "$result" '"consumer_alive"'

# 2. Webhook 接收（無 Token 驗證模式）
echo ""
echo "[2] Webhook 接收 /vrops-webhook"
result=$(curl -s -X POST "$BASE_URL/vrops-webhook" \
    -H "Content-Type: application/json" \
    -d '{"alertName":"CPU High","resourceName":"test-vm-01","criticality":"CRITICAL","info":"CPU 使用率 95%"}')
check "webhook accepted" "$result" '"status": "accepted"'

# 3. 重複告警去重
echo ""
echo "[3] 重複告警去重"
result=$(curl -s -X POST "$BASE_URL/vrops-webhook" \
    -H "Content-Type: application/json" \
    -d '{"alertName":"CPU High","resourceName":"test-vm-01","criticality":"CRITICAL","info":"CPU 使用率 95%"}')
check "duplicate ignored" "$result" '"status": "duplicate_ignored"'

# 4. 無效 JSON
echo ""
echo "[4] 無效 JSON 輸入"
result=$(curl -s -X POST "$BASE_URL/vrops-webhook" \
    -H "Content-Type: application/json" \
    -d 'not-json')
check "invalid json rejected" "$result" '"error": "invalid json"'

# 5. WebGUI 登入頁
echo ""
echo "[5] WebGUI 登入頁"
result=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/login")
check "login page accessible" "$result" "200"

# 6. WebGUI 未登入保護
echo ""
echo "[6] WebGUI 未登入保護"
result=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/")
check "dashboard redirects to login" "$result" "302"

# 7. API 未登入保護
echo ""
echo "[7] API 未登入保護"
result=$(curl -s -H "Accept: application/json" "$BASE_URL/api/contacts")
check "api unauthorized" "$result" '"error"'

# 結果摘要
echo ""
echo "================================"
echo "測試結果: PASS=$PASS FAIL=$FAIL"
echo "================================"

if [ "$FAIL" -eq 0 ]; then
    echo "所有冒煙測試通過！"
    exit 0
else
    echo "有 $FAIL 項測試失敗，請檢查上方輸出。"
    exit 1
fi
