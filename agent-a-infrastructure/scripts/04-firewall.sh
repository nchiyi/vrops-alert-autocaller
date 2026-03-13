#!/usr/bin/env bash
# =============================================================
# Agent A — Step 4: 防火牆設定
# =============================================================
set -euo pipefail

echo "=== 設定 UFW 防火牆規則 ==="

# 確保 SSH 不被鎖住
sudo ufw allow OpenSSH

# 開放 Webhook 接收埠（供 vROps 推送告警）
sudo ufw allow 5000/tcp comment "vROps Webhook"

# SIP TLS 出站通常不需要開放 inbound，
# 但若有特殊 NAT 環境，可視需求取消以下注解：
# sudo ufw allow 5061/tcp comment "SIP TLS outbound"

# 啟用防火牆（如已啟用則重新載入）
echo "y" | sudo ufw enable || true
sudo ufw reload

echo ""
echo "=== 目前防火牆規則 ==="
sudo ufw status verbose

echo ""
echo "✅ Step 4 完成：防火牆設定完成"
echo "   下一步：將 systemd/vrops-alert-caller.service 複製到 /etc/systemd/system/"
