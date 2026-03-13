#!/usr/bin/env bash
# =============================================================
# vROps Alert AutoCaller — 一鍵自動安裝部署腳本
# 支援：Ubuntu 22.04 LTS | Docker 模式
# 用法：
#   bash install.sh              # 互動式安裝（systemd）
#   bash install.sh --docker     # Docker Compose 安裝
#   bash install.sh --uninstall  # 移除服務
# =============================================================
set -euo pipefail

# ────────────────────────────────────────────────
# 全域常數
# ────────────────────────────────────────────────
INSTALL_DIR="/opt/vrops-alert-caller"
SERVICE_USER="vrops-alert"
SERVICE_NAME="vrops-alert-caller"
VENV_DIR="$INSTALL_DIR/venv"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; NC='\033[0m'

log_info()    { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $*"; }
log_section() { echo -e "\n${CYAN}════════════════════════════════════════${NC}"; echo -e "${CYAN}  $*${NC}"; echo -e "${CYAN}════════════════════════════════════════${NC}"; }

# ────────────────────────────────────────────────
# 前置檢查
# ────────────────────────────────────────────────
check_root() {
    if [[ $EUID -ne 0 ]]; then
        log_error "請以 root 執行（sudo bash install.sh）"
        exit 1
    fi
}

check_os() {
    if ! grep -qi "ubuntu" /etc/os-release 2>/dev/null; then
        log_warn "此腳本針對 Ubuntu 22.04 最佳化，其他系統可能需要手動調整"
    fi
    OS_VERSION=$(grep VERSION_ID /etc/os-release | cut -d'"' -f2 2>/dev/null || echo "unknown")
    log_info "作業系統版本：$OS_VERSION"
}

# ────────────────────────────────────────────────
# 互動式設定收集
# ────────────────────────────────────────────────
collect_config() {
    log_section "環境參數設定"
    echo -e "${YELLOW}請輸入以下設定（Enter 使用預設值）${NC}\n"

    # SIP 設定
    echo -e "${BLUE}--- SIP 撥號設定 (EZUC+) ---${NC}"
    read -r -p "SIP 伺服器 [clouduc.e-usi.com]: " SIP_SERVER
    SIP_SERVER="${SIP_SERVER:-clouduc.e-usi.com}"

    read -r -p "SIP Port [5061]: " SIP_PORT
    SIP_PORT="${SIP_PORT:-5061}"

    read -r -p "SIP 帳號: " SIP_USER
    while [[ -z "$SIP_USER" ]]; do
        log_warn "SIP 帳號不能為空"
        read -r -p "SIP 帳號: " SIP_USER
    done

    read -r -s -p "SIP 密碼: " SIP_PASS; echo
    while [[ -z "$SIP_PASS" ]]; do
        log_warn "SIP 密碼不能為空"
        read -r -s -p "SIP 密碼: " SIP_PASS; echo
    done

    # Webhook Token
    echo -e "\n${BLUE}--- Webhook 驗證 ---${NC}"
    DEFAULT_TOKEN=$(python3 -c "import secrets; print(secrets.token_hex(24))" 2>/dev/null || cat /dev/urandom | tr -dc 'a-zA-Z0-9' | head -c 48)
    read -r -p "Webhook Auth Token [自動產生]: " WEBHOOK_TOKEN
    WEBHOOK_TOKEN="${WEBHOOK_TOKEN:-$DEFAULT_TOKEN}"

    read -r -p "Webhook Port [5000]: " WEBHOOK_PORT
    WEBHOOK_PORT="${WEBHOOK_PORT:-5000}"

    # WebGUI 管理員帳密
    echo -e "\n${BLUE}--- WebGUI 管理介面 ---${NC}"
    read -r -p "管理員帳號 [admin]: " ADMIN_USER
    ADMIN_USER="${ADMIN_USER:-admin}"

    read -r -s -p "管理員密碼: " ADMIN_PASS; echo
    while [[ ${#ADMIN_PASS} -lt 6 ]]; do
        log_warn "密碼至少 6 個字元"
        read -r -s -p "管理員密碼: " ADMIN_PASS; echo
    done

    # Flask Secret Key（隨機產生）
    SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || cat /dev/urandom | tr -dc 'a-zA-Z0-9' | head -c 64)

    # TTS 設定
    echo -e "\n${BLUE}--- TTS 語音引擎 ---${NC}"
    echo "  1) edge-tts（推薦，需網路）"
    echo "  2) gtts（需網路）"
    echo "  3) pyttsx3（離線，品質較低）"
    read -r -p "選擇 TTS 引擎 [1]: " TTS_CHOICE
    case "${TTS_CHOICE:-1}" in
        2) TTS_ENGINE="gtts" ;;
        3) TTS_ENGINE="pyttsx3" ;;
        *) TTS_ENGINE="edge-tts" ;;
    esac

    # 告警參數
    echo -e "\n${BLUE}--- 告警處理參數 ---${NC}"
    read -r -p "去重視窗秒數 [300]: " DEDUP_WINDOW
    DEDUP_WINDOW="${DEDUP_WINDOW:-300}"

    read -r -p "未接最多重撥次數 [3]: " MAX_RETRY
    MAX_RETRY="${MAX_RETRY:-3}"

    read -r -p "重撥間隔秒數 [120]: " RETRY_INTERVAL
    RETRY_INTERVAL="${RETRY_INTERVAL:-120}"

    # 確認設定
    echo -e "\n${CYAN}═══════ 設定確認 ═══════${NC}"
    echo "  SIP 伺服器:    $SIP_SERVER:$SIP_PORT"
    echo "  SIP 帳號:      $SIP_USER"
    echo "  Webhook Port:  $WEBHOOK_PORT"
    echo "  TTS 引擎:      $TTS_ENGINE"
    echo "  管理員帳號:    $ADMIN_USER"
    echo ""
    read -r -p "確認以上設定並開始安裝？[y/N] " CONFIRM
    if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
        log_warn "已取消安裝"
        exit 0
    fi
}

# ────────────────────────────────────────────────
# 寫入 settings.yaml
# ────────────────────────────────────────────────
write_settings_yaml() {
    log_info "寫入 settings.yaml ..."
    cat > "$INSTALL_DIR/config/settings.yaml" <<YAML
# vROps Alert AutoCaller — 自動產生的設定檔
# 產生時間：$(date '+%Y-%m-%d %H:%M:%S')
# 修改後執行：systemctl restart $SERVICE_NAME

webhook:
  host: "0.0.0.0"
  port: $WEBHOOK_PORT
  auth_token: "$WEBHOOK_TOKEN"

tts:
  engine: "$TTS_ENGINE"
  voice: "zh-TW-HsiaoChenNeural"
  output_dir: "$INSTALL_DIR/audio"
  fallback_wav: "$INSTALL_DIR/audio/fallback_alert.wav"

sip:
  server: "$SIP_SERVER"
  port: $SIP_PORT
  transport: "tls"
  username: "$SIP_USER"
  password: "$SIP_PASS"

alert:
  dedup_window_seconds: $DEDUP_WINDOW
  max_retry: $MAX_RETRY
  retry_interval_seconds: $RETRY_INTERVAL
  escalation: true
  queue_ttl_seconds: 600
  batch_threshold: 3

webgui:
  secret_key: "$SECRET_KEY"
  users:
    $ADMIN_USER: "$ADMIN_PASS"

logging:
  file: "$INSTALL_DIR/logs/app.log"
  level: "INFO"
  db_path: "$INSTALL_DIR/logs/alerts.db"
YAML
    chmod 600 "$INSTALL_DIR/config/settings.yaml"
    chown "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR/config/settings.yaml"
    log_info "settings.yaml 寫入完成"
}

# ────────────────────────────────────────────────
# Step 1: 安裝系統依賴
# ────────────────────────────────────────────────
install_system_deps() {
    log_section "Step 1/6：安裝系統依賴套件"

    apt-get update -q

    # ── 確保 Python 3.11+ 可用 ──────────────────────
    # Ubuntu 20.04 / 22.04 預設 apt 沒有 python3.11，需要加 deadsnakes PPA
    PY_BIN=""
    for bin in python3.11 python3.12 python3.13; do
        if command -v "$bin" &>/dev/null; then
            PY_BIN="$bin"
            break
        fi
    done

    if [[ -z "$PY_BIN" ]]; then
        log_info "偵測不到 Python 3.11+，嘗試從 deadsnakes PPA 安裝..."
        apt-get install -y -q software-properties-common
        add-apt-repository -y ppa:deadsnakes/ppa
        apt-get update -q
        apt-get install -y -q python3.11 python3.11-venv python3.11-dev
        PY_BIN="python3.11"
    else
        log_info "偵測到 $PY_BIN，直接使用"
        # 確保 venv 模組也裝上
        PYVER="${PY_BIN##python}"   # e.g. "3.11"
        apt-get install -y -q "${PY_BIN}-venv" "${PY_BIN}-dev" 2>/dev/null || true
    fi

    # 將選定的 Python 寫入環境變數，後續步驟共用
    export PYTHON_BIN="$PY_BIN"
    log_info "使用 Python：$(${PY_BIN} --version)"

    apt-get install -y -q \
        python3-pip \
        build-essential \
        libssl-dev \
        libasound2-dev \
        libopus-dev \
        libv4l-dev \
        swig \
        ffmpeg \
        espeak-ng \
        git \
        curl \
        wget \
        ufw

    log_info "系統依賴安裝完成"
}

# ────────────────────────────────────────────────
# Step 2: 編譯 pjsua2
# ────────────────────────────────────────────────
install_pjsua2() {
    log_section "Step 2/6：編譯安裝 pjsua2（SIP TLS 支援）"
    log_warn "此步驟預計耗時 10-20 分鐘，請勿中斷..."

    # 已安裝則跳過
    if python3 -c "import pjsua2" 2>/dev/null; then
        log_info "pjsua2 已安裝，跳過編譯"
        return 0
    fi

    PJPROJECT_DIR="/tmp/pjproject"
    if [[ ! -d "$PJPROJECT_DIR" ]]; then
        log_info "下載 pjproject 原始碼..."
        git clone --depth 1 https://github.com/pjsip/pjproject.git "$PJPROJECT_DIR"
    fi

    cd "$PJPROJECT_DIR"
    log_info "設定編譯選項..."
    ./configure \
        --enable-shared \
        --with-ssl=/usr \
        --prefix=/usr/local \
        CFLAGS="-fPIC"

    log_info "開始編譯（$(nproc) 核心）..."
    make -j"$(nproc)"
    make install
    ldconfig

    log_info "編譯 Python binding..."
    cd pjsip-apps/src/swig/python
    make
    make install
    ldconfig

    # 驗證
    if python3 -c "import pjsua2; print('pjsua2 OK')"; then
        log_info "pjsua2 安裝驗證通過"
    else
        log_error "pjsua2 import 失敗！請手動檢查編譯輸出"
        exit 1
    fi
}

# ────────────────────────────────────────────────
# Step 3: 建立服務帳號與目錄
# ────────────────────────────────────────────────
setup_directories() {
    log_section "Step 3/6：建立服務帳號與目錄"

    # 建立系統帳號
    if ! id "$SERVICE_USER" &>/dev/null; then
        useradd -r -s /bin/false -d "$INSTALL_DIR" "$SERVICE_USER"
        log_info "建立服務帳號：$SERVICE_USER"
    else
        log_info "服務帳號 $SERVICE_USER 已存在"
    fi

    # 建立目錄
    mkdir -p "$INSTALL_DIR"/{config,logs,audio,web}

    # 複製應用程式檔案
    log_info "複製應用程式..."
    cp -f "$SCRIPT_DIR/webhook_server.py" "$INSTALL_DIR/"
    cp -f "$SCRIPT_DIR/tts_engine.py"     "$INSTALL_DIR/"
    cp -f "$SCRIPT_DIR/sip_caller.py"     "$INSTALL_DIR/"
    cp -f "$SCRIPT_DIR/alert_manager.py"  "$INSTALL_DIR/"
    cp -f "$SCRIPT_DIR/routing_engine.py" "$INSTALL_DIR/"

    # 複製 web 目錄
    cp -rf "$SCRIPT_DIR/web/"      "$INSTALL_DIR/"

    # 設定權限
    chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
    chmod 755 "$INSTALL_DIR"
    chmod 700 "$INSTALL_DIR/logs"
    chmod 750 "$INSTALL_DIR/audio"

    log_info "目錄結構建立完成：$INSTALL_DIR"
}

# ────────────────────────────────────────────────
# Step 4: 建立 Python 虛擬環境
# ────────────────────────────────────────────────
setup_venv() {
    log_section "Step 4/6：建立 Python 虛擬環境"

    if [[ ! -d "$VENV_DIR" ]]; then
        # PYTHON_BIN 由 install_system_deps() 設定；若直接呼叫此函式則自動偵測
        if [[ -z "${PYTHON_BIN:-}" ]]; then
            for bin in python3.11 python3.12 python3.13 python3; do
                if command -v "$bin" &>/dev/null && "$bin" -c "import venv" 2>/dev/null; then
                    PYTHON_BIN="$bin"; break
                fi
            done
        fi
        log_info "建立虛擬環境 (${PYTHON_BIN})..."
        sudo -u "$SERVICE_USER" "$PYTHON_BIN" -m venv "$VENV_DIR"
        log_info "虛擬環境建立：$VENV_DIR"
    else
        log_info "虛擬環境已存在，更新套件..."
    fi

    log_info "安裝 Python 套件..."
    sudo -u "$SERVICE_USER" "$VENV_DIR/bin/pip" install --upgrade pip -q
    sudo -u "$SERVICE_USER" "$VENV_DIR/bin/pip" install -q \
        "flask>=3.0.0" \
        "gunicorn>=21.2.0" \
        "pyyaml>=6.0.1" \
        "edge-tts>=6.1.10" \
        "gtts>=2.5.0" \
        "pydub>=0.25.1" \
        "requests>=2.31.0" \
        "pyttsx3>=2.90"

    # 驗證
    "$VENV_DIR/bin/python" -c "import flask, yaml, gtts, pydub; print('Python 套件 OK')"
    log_info "Python 套件安裝完成"
}

# ────────────────────────────────────────────────
# Step 5: systemd + logrotate + 防火牆
# ────────────────────────────────────────────────
setup_services() {
    log_section "Step 5/6：設定 systemd / logrotate / 防火牆"

    # 產生 settings.yaml（已互動收集完畢）
    write_settings_yaml

    # systemd service
    log_info "設定 systemd service..."
    cat > "/etc/systemd/system/$SERVICE_NAME.service" <<EOF
[Unit]
Description=vROps Alert AutoCaller
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR
Environment=PATH=$VENV_DIR/bin:/usr/local/bin:/usr/bin:/bin
Environment=PYTHONUNBUFFERED=1
ExecStart=$VENV_DIR/bin/gunicorn \\
    --bind 0.0.0.0:$WEBHOOK_PORT \\
    --workers 1 \\
    --threads 4 \\
    --timeout 120 \\
    --access-logfile $INSTALL_DIR/logs/access.log \\
    --error-logfile $INSTALL_DIR/logs/gunicorn-error.log \\
    webhook_server:app
Restart=always
RestartSec=10
LimitNOFILE=65536
MemoryMax=512M

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"
    log_info "systemd service 設定完成"

    # logrotate
    log_info "設定 logrotate..."
    cat > "/etc/logrotate.d/$SERVICE_NAME" <<EOF
$INSTALL_DIR/logs/*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    create 0640 $SERVICE_USER $SERVICE_USER
    postrotate
        systemctl reload $SERVICE_NAME 2>/dev/null || true
    endscript
}
EOF

    # 防火牆
    if command -v ufw &>/dev/null; then
        log_info "設定防火牆規則..."
        ufw allow "$WEBHOOK_PORT/tcp" comment "vROps Webhook" 2>/dev/null || true
        ufw allow 5061/tcp comment "SIP TLS" 2>/dev/null || true
        log_info "防火牆規則已加入"
    fi

    # 產生備援語音
    log_info "產生備援語音（TTS 失敗時的備援）..."
    sudo -u "$SERVICE_USER" "$VENV_DIR/bin/python" \
        "$INSTALL_DIR/tts_engine.py" --generate-fallback 2>/dev/null || \
        log_warn "備援語音產生失敗（非致命，服務仍可啟動）"
}

# ────────────────────────────────────────────────
# Step 6: 啟動服務並驗證
# ────────────────────────────────────────────────
start_and_verify() {
    log_section "Step 6/6：啟動服務並驗證"

    systemctl start "$SERVICE_NAME"
    log_info "等待服務啟動（10 秒）..."
    sleep 10

    if systemctl is-active --quiet "$SERVICE_NAME"; then
        log_info "服務啟動成功"
    else
        log_error "服務啟動失敗！請執行：journalctl -u $SERVICE_NAME -n 50"
        exit 1
    fi

    # 健康檢查
    HEALTH=$(curl -sf "http://localhost:$WEBHOOK_PORT/health" 2>/dev/null || echo "FAILED")
    if echo "$HEALTH" | grep -q '"status"'; then
        log_info "健康檢查通過"
    else
        log_warn "健康檢查未通過（服務可能還在初始化，可稍後執行 bash tests/test_smoke.sh）"
    fi
}

# ────────────────────────────────────────────────
# Docker 模式安裝
# ────────────────────────────────────────────────
install_docker_mode() {
    log_section "Docker 模式安裝"

    # 確認 Docker 已安裝
    if ! command -v docker &>/dev/null; then
        log_info "安裝 Docker..."
        curl -fsSL https://get.docker.com | sh
        usermod -aG docker "$SUDO_USER" 2>/dev/null || true
    fi
    if ! command -v docker &>/dev/null || ! docker compose version &>/dev/null; then
        log_error "Docker Compose v2 未安裝，請先安裝後重試"
        exit 1
    fi

    # 收集設定
    collect_config

    # 在腳本目錄產生 settings.yaml（由 docker-compose volume 掛載）
    INSTALL_DIR="$SCRIPT_DIR"
    mkdir -p "$SCRIPT_DIR/config" "$SCRIPT_DIR/logs" "$SCRIPT_DIR/audio"
    write_settings_yaml

    # 建置並啟動
    log_info "建置 Docker 映像檔（首次約 10-20 分鐘）..."
    cd "$SCRIPT_DIR"
    docker compose -f docker/docker-compose.yml build

    log_info "啟動容器..."
    docker compose -f docker/docker-compose.yml up -d

    sleep 10
    if docker compose -f docker/docker-compose.yml ps | grep -q "Up\|running"; then
        log_info "容器啟動成功"
    else
        log_error "容器啟動失敗，請執行：docker compose -f docker/docker-compose.yml logs"
        exit 1
    fi

    print_final_info_docker
}

# ────────────────────────────────────────────────
# 移除服務
# ────────────────────────────────────────────────
uninstall() {
    log_section "移除 vROps Alert AutoCaller"
    read -r -p "確認移除服務和所有資料？[y/N] " CONFIRM
    if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
        log_warn "取消移除"
        exit 0
    fi

    systemctl stop "$SERVICE_NAME"   2>/dev/null || true
    systemctl disable "$SERVICE_NAME" 2>/dev/null || true
    rm -f "/etc/systemd/system/$SERVICE_NAME.service"
    rm -f "/etc/logrotate.d/$SERVICE_NAME"
    systemctl daemon-reload

    read -r -p "同時刪除 $INSTALL_DIR（含日誌和資料庫）？[y/N] " DEL_DATA
    if [[ "$DEL_DATA" =~ ^[Yy]$ ]]; then
        rm -rf "$INSTALL_DIR"
        log_info "$INSTALL_DIR 已刪除"
    fi

    log_info "移除完成"
}

# ────────────────────────────────────────────────
# 安裝完成說明
# ────────────────────────────────────────────────
print_final_info() {
    echo ""
    echo -e "${GREEN}╔══════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║   vROps Alert AutoCaller 安裝完成！                 ║${NC}"
    echo -e "${GREEN}╚══════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "  ${CYAN}Webhook URL：${NC}  http://$(hostname -I | awk '{print $1}'):$WEBHOOK_PORT/vrops-webhook"
    echo -e "  ${CYAN}WebGUI URL：${NC}   http://$(hostname -I | awk '{print $1}'):$WEBHOOK_PORT"
    echo -e "  ${CYAN}管理員帳號：${NC}   $ADMIN_USER"
    echo -e "  ${CYAN}健康檢查：${NC}     http://$(hostname -I | awk '{print $1}'):$WEBHOOK_PORT/health"
    echo ""
    echo -e "  ${YELLOW}下一步：${NC}"
    echo "  1. 在 vROps 設定 Webhook URL 和 Authorization: Bearer $WEBHOOK_TOKEN"
    echo "  2. 開啟 WebGUI 新增聯絡人和路由規則"
    echo "  3. 執行冒煙測試：bash $SCRIPT_DIR/tests/test_smoke.sh"
    echo ""
    echo -e "  ${CYAN}常用指令：${NC}"
    echo "  systemctl status $SERVICE_NAME    # 查看服務狀態"
    echo "  journalctl -u $SERVICE_NAME -f    # 即時日誌"
    echo "  systemctl restart $SERVICE_NAME   # 重啟服務"
    echo "  nano $INSTALL_DIR/config/settings.yaml  # 修改設定"
    echo ""
}

print_final_info_docker() {
    echo ""
    echo -e "${GREEN}╔══════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║   vROps Alert AutoCaller (Docker) 安裝完成！        ║${NC}"
    echo -e "${GREEN}╚══════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "  ${CYAN}Webhook URL：${NC}  http://$(hostname -I | awk '{print $1}'):$WEBHOOK_PORT/vrops-webhook"
    echo -e "  ${CYAN}WebGUI URL：${NC}   http://$(hostname -I | awk '{print $1}'):$WEBHOOK_PORT"
    echo -e "  ${CYAN}管理員帳號：${NC}   $ADMIN_USER"
    echo ""
    echo -e "  ${YELLOW}Docker 常用指令：${NC}"
    echo "  docker compose -f docker/docker-compose.yml logs -f   # 即時日誌"
    echo "  docker compose -f docker/docker-compose.yml restart   # 重啟"
    echo "  docker compose -f docker/docker-compose.yml down      # 停止"
    echo ""
}

# ────────────────────────────────────────────────
# 主流程
# ────────────────────────────────────────────────
main() {
    echo -e "${CYAN}"
    echo "  ╦  ╦╦═╗╔═╗╔═╗╔═╗  ╔═╗╦  ╔═╗╦═╗╔╦╗  ╔═╗╦ ╦╔╦╗╔═╗╔═╗╔═╗╦  ╦  ╔═╗╦═╗"
    echo "  ╚╗╔╝╠╦╝║ ║╠═╝╚═╗  ╠═╣║  ║╣ ╠╦╝ ║   ╠═╣║ ║ ║ ║ ║║  ╠═╣║  ║  ║╣ ╠╦╝"
    echo "   ╚╝ ╩╚═╚═╝╩  ╚═╝  ╩ ╩╩═╝╚═╝╩╚═ ╩   ╩ ╩╚═╝ ╩ ╚═╝╚═╝╩ ╩╩═╝╩═╝╚═╝╩╚═"
    echo -e "${NC}"
    echo -e "  ${GREEN}vROps Alert AutoCaller — 自動安裝腳本 v1.0${NC}"
    echo ""

    # 解析參數
    MODE="systemd"
    for arg in "$@"; do
        case "$arg" in
            --docker)     MODE="docker" ;;
            --uninstall)  MODE="uninstall" ;;
        esac
    done

    case "$MODE" in
        docker)
            install_docker_mode
            ;;
        uninstall)
            check_root
            uninstall
            ;;
        systemd)
            check_root
            check_os
            collect_config
            install_system_deps
            install_pjsua2
            setup_directories
            setup_venv
            setup_services
            start_and_verify
            print_final_info
            ;;
    esac
}

main "$@"
