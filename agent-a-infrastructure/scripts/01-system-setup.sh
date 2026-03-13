#!/usr/bin/env bash
# =============================================================
# Agent A — Step 1: 系統基礎環境安裝
# Ubuntu 22.04 LTS
# =============================================================
set -euo pipefail

echo "=== [1/4] 系統更新 ==="
sudo apt update && sudo apt upgrade -y

echo "=== [2/4] 安裝系統依賴套件 ==="
sudo apt install -y \
    python3.11 \
    python3.11-venv \
    python3-pip \
    build-essential \
    libssl-dev \
    libasound2-dev \
    libopus-dev \
    swig \
    python3-dev \
    ffmpeg \
    espeak-ng \
    git \
    curl \
    wget \
    ufw

echo "=== [3/4] 建立服務帳號 ==="
if ! id "vrops-alert" &>/dev/null; then
    sudo useradd -r -s /bin/false vrops-alert
    echo "已建立帳號: vrops-alert"
else
    echo "帳號 vrops-alert 已存在，跳過"
fi

echo "=== [4/4] 建立專案目錄結構 ==="
sudo mkdir -p /opt/vrops-alert-caller/{config,logs,audio,web}
sudo chown -R vrops-alert:vrops-alert /opt/vrops-alert-caller

echo ""
echo "✅ Step 1 完成：系統基礎環境就緒"
echo "   下一步：執行 02-install-pjsua2.sh 編譯 pjsua2"
