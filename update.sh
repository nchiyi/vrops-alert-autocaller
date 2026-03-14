#!/bin/bash
# =============================================================
# vROps Alert AutoCaller — 一鍵更新腳本
# 用法：sudo bash /opt/vrops-alert-caller/update.sh
# =============================================================

set -e

DIR="/opt/vrops-alert-caller"
SERVICE="vrops-alert-caller"
VENV="$DIR/venv/bin"

echo "=== [1/4] 拉取最新程式碼 ==="
# 捨棄受追蹤檔案的本地修改（settings.yaml 在 .gitignore 中，不受影響）
git -C "$DIR" checkout -- . 2>/dev/null || true
git -C "$DIR" pull origin main

echo "=== [2/4] 修正檔案權限 ==="
chown -R vrops-alert:vrops-alert "$DIR/config/"
chmod 640 "$DIR/config/settings.yaml" 2>/dev/null || true
# ssl_helper.sh 必須 root 擁有且 chmod 700，才能由 sudo 安全呼叫
if [[ -f "$DIR/ssl_helper.sh" ]]; then
    chown root:root "$DIR/ssl_helper.sh"
    chmod 700 "$DIR/ssl_helper.sh"
fi

echo "=== [3/4] 更新 Python 套件 ==="
"$VENV/pip" install -q -r "$DIR/requirements.txt"

echo "=== [4/4] 重啟服務 ==="
systemctl restart "$SERVICE"
sleep 2
systemctl status "$SERVICE" --no-pager | head -15

echo ""
echo "✓ 更新完成"
