#!/usr/bin/env bash
# =============================================================
# Agent A — Step 5: 部署 systemd service + logrotate
# 需先完成 Agent B/C/D 程式碼放置後再執行啟動
# =============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

echo "=== [1/4] 安裝 systemd service ==="
sudo cp "$REPO_ROOT/systemd/vrops-alert-caller.service" \
    /etc/systemd/system/vrops-alert-caller.service
sudo systemctl daemon-reload
echo "✅ service 檔案已安裝"

echo "=== [2/4] 安裝 logrotate 設定 ==="
sudo cp "$REPO_ROOT/logrotate/vrops-alert-caller" \
    /etc/logrotate.d/vrops-alert-caller
echo "✅ logrotate 已設定"

echo "=== [3/4] 安裝 cron jobs ==="
sudo cp "$REPO_ROOT/logrotate/vrops-cron" \
    /etc/cron.d/vrops-cron
sudo chmod 644 /etc/cron.d/vrops-cron
echo "✅ cron jobs 已設定"

echo "=== [4/4] 設定目錄權限 ==="
sudo chown -R vrops-alert:vrops-alert /opt/vrops-alert-caller
sudo chmod 750 /opt/vrops-alert-caller
sudo chmod 640 /opt/vrops-alert-caller/config/settings.yaml 2>/dev/null || true

echo ""
echo "✅ Step 5 完成：service + logrotate + cron 已就位"
echo ""
echo "⚠️  啟動前請確認："
echo "   1. /opt/vrops-alert-caller/config/settings.yaml 已填入 SIP 帳密"
echo "   2. Agent B/C/D 的程式碼已複製到 /opt/vrops-alert-caller/"
echo ""
echo "   確認後執行："
echo "   sudo systemctl enable --now vrops-alert-caller"
echo "   sudo systemctl status vrops-alert-caller"
