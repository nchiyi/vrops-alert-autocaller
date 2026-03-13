#!/usr/bin/env bash
# =============================================================
# Agent A — Step 3: 建立 Python 虛擬環境 + 安裝套件
# =============================================================
set -euo pipefail

INSTALL_DIR="/opt/vrops-alert-caller"
VENV_DIR="$INSTALL_DIR/venv"

echo "=== [1/3] 建立 Python 3.11 虛擬環境 ==="
sudo -u vrops-alert python3.11 -m venv "$VENV_DIR"

echo "=== [2/3] 安裝 Python 套件 ==="
sudo -u vrops-alert "$VENV_DIR/bin/pip" install --upgrade pip

sudo -u vrops-alert "$VENV_DIR/bin/pip" install \
    "flask==3.0.*" \
    "gunicorn==22.*" \
    "pyyaml==6.*" \
    "gtts==2.*" \
    "edge-tts==6.*" \
    "pydub==0.25.*" \
    "requests==2.*" \
    "pyttsx3"

echo "=== [3/3] 驗證套件安裝 ==="
"$VENV_DIR/bin/python" -c "
import flask, gunicorn, yaml, gtts, pydub, requests
print('✅ 所有 Python 套件 import OK')
"

# edge-tts 需要非同步，另外驗證
"$VENV_DIR/bin/python" -c "import edge_tts; print('✅ edge-tts OK')"

echo ""
echo "✅ Step 3 完成：Python 虛擬環境就緒"
echo "   虛擬環境路徑：$VENV_DIR"
echo "   下一步：執行 04-firewall.sh 設定防火牆"
