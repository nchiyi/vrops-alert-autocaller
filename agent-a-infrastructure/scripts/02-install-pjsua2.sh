#!/usr/bin/env bash
# =============================================================
# Agent A — Step 2: 編譯安裝 pjsua2（支援 SIP TLS）
# 預估耗時：10-20 分鐘（視 CPU 而定）
# =============================================================
set -euo pipefail

PJPROJECT_DIR="/tmp/pjproject"

echo "=== [1/5] 安裝編譯依賴 ==="
sudo apt install -y \
    build-essential \
    libssl-dev \
    libasound2-dev \
    libopus-dev \
    libv4l-dev \
    swig \
    python3-dev \
    git

echo "=== [2/5] 下載 pjproject 原始碼 ==="
if [ -d "$PJPROJECT_DIR" ]; then
    echo "已存在 $PJPROJECT_DIR，跳過下載"
else
    git clone --depth 1 https://github.com/pjsip/pjproject.git "$PJPROJECT_DIR"
fi

cd "$PJPROJECT_DIR"

echo "=== [3/5] 設定編譯選項（啟用 TLS + shared library + fPIC）==="
./configure \
    --enable-shared \
    --with-ssl=/usr \
    --prefix=/usr/local \
    CFLAGS="-fPIC"

echo "=== [4/5] 編譯（使用 $(nproc) 核心）==="
make -j"$(nproc)"
sudo make install
sudo ldconfig

echo "=== [5/5] 編譯 Python binding (pjsua2) ==="
cd pjsip-apps/src/swig/python
make
sudo make install
sudo ldconfig

echo ""
echo "=== 驗證 pjsua2 ==="
python3 -c "import pjsua2; print('✅ pjsua2 import OK')" || {
    echo "❌ pjsua2 import 失敗，請檢查："
    echo "   1. libssl-dev 是否已安裝（影響 TLS 支援）"
    echo "   2. ldconfig 是否已執行"
    exit 1
}

echo ""
echo "✅ Step 2 完成：pjsua2 編譯安裝成功"
echo "   下一步：執行 03-setup-venv.sh 建立 Python 虛擬環境"
