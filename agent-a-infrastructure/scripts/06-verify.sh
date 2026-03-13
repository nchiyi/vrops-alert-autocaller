#!/usr/bin/env bash
# =============================================================
# Agent A — Step 6: 驗證環境清單
# =============================================================
set -euo pipefail

VENV="/opt/vrops-alert-caller/venv/bin/python"
PASS=0
FAIL=0

check() {
    local label="$1"
    local cmd="$2"
    if eval "$cmd" &>/dev/null; then
        echo "  ✅ $label"
        PASS=$((PASS+1))
    else
        echo "  ❌ $label"
        FAIL=$((FAIL+1))
    fi
}

echo "=== Agent A 環境驗證清單 ==="
echo ""
echo "--- 系統工具 ---"
check "python3.11 已安裝"          "python3.11 --version"
check "ffmpeg 已安裝"               "ffmpeg -version"
check "espeak-ng 已安裝"            "espeak-ng --version"
check "git 已安裝"                  "git --version"

echo ""
echo "--- Python 套件（虛擬環境）---"
check "flask"      "$VENV -c 'import flask'"
check "gunicorn"   "$VENV -c 'import gunicorn'"
check "pyyaml"     "$VENV -c 'import yaml'"
check "gtts"       "$VENV -c 'import gtts'"
check "edge-tts"   "$VENV -c 'import edge_tts'"
check "pydub"      "$VENV -c 'import pydub'"
check "requests"   "$VENV -c 'import requests'"
check "pjsua2"     "$VENV -c 'import pjsua2' 2>/dev/null || python3 -c 'import pjsua2'"

echo ""
echo "--- 目錄結構 ---"
check "/opt/vrops-alert-caller 存在"       "test -d /opt/vrops-alert-caller"
check "/opt/vrops-alert-caller/audio 存在" "test -d /opt/vrops-alert-caller/audio"
check "/opt/vrops-alert-caller/logs 存在"  "test -d /opt/vrops-alert-caller/logs"
check "/opt/vrops-alert-caller/config 存在" "test -d /opt/vrops-alert-caller/config"
check "settings.yaml 存在"                 "test -f /opt/vrops-alert-caller/config/settings.yaml"

echo ""
echo "--- 服務帳號 ---"
check "vrops-alert 帳號存在" "id vrops-alert"

echo ""
echo "--- 防火牆 ---"
check "port 5000 已開放" "sudo ufw status | grep -q '5000/tcp'"

echo ""
echo "--- 網路連線 ---"
check "DNS 解析正常" "getent hosts clouduc.e-usi.com"

echo ""
echo "=== 結果：${PASS} 項通過 / ${FAIL} 項失敗 ==="
if [ "$FAIL" -gt 0 ]; then
    echo "⚠️  請修正失敗項目後重新執行驗證"
    exit 1
else
    echo "🎉 所有驗證通過，Agent A 環境就緒！"
fi
