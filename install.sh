#!/usr/bin/env bash
# =============================================================
# vROps Alert AutoCaller — 一鍵自動安裝部署腳本
# 支援：Ubuntu 22.04 LTS | Docker 模式
# 用法：
#   bash install.sh              # 互動式安裝（systemd）
#   bash install.sh --docker     # Docker Compose 安裝
#   bash install.sh --uninstall  # 移除服務
#   bash install.sh --update     # 從 GitHub 拉取最新程式碼並重啟
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
REPO_URL="https://github.com/nchiyi/vrops-alert-autocaller.git"

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

    # ════════════════════════════════════════════════
    # 既有安裝偵測：若 settings.yaml 已存在，詢問是否保留
    # ════════════════════════════════════════════════
    _WRITE_SETTINGS=true
    if [[ -f "$INSTALL_DIR/config/settings.yaml" ]]; then
        echo ""
        log_warn "偵測到既有設定檔：$INSTALL_DIR/config/settings.yaml"
        echo -e "  ${CYAN}選項：${NC}"
        echo "    1) 保留現有設定（僅更新程式碼與系統依賴，不修改 settings.yaml）[推薦]"
        echo "    2) 重新設定（現有 settings.yaml 將備份後覆蓋）"
        echo ""
        read -r -p "請選擇 [1]: " _REINSTALL_CHOICE
        if [[ ! "${_REINSTALL_CHOICE:-1}" =~ ^2$ ]]; then
            log_info "→ 保留現有設定，跳過設定輸入步驟"
            _WRITE_SETTINGS=false
            # 從現有 settings.yaml 讀取必要變數（供後續步驟使用）
            WEBHOOK_PORT=$(grep -A5 '^webhook:' "$INSTALL_DIR/config/settings.yaml" \
                           | awk '/port:/{print $2; exit}')
            WEBHOOK_PORT="${WEBHOOK_PORT:-5000}"
            WEBHOOK_TOKEN="(保留現有)"
            ADMIN_USER="(保留現有)"
            TTS_ENGINE="(保留現有)"
            CALL_BACKEND="(保留現有)"
            # nginx 仍需詢問（OS 層面，與 settings.yaml 無關）
            echo ""
            echo -e "${BLUE}--- nginx 設定（OS 層面，與 settings.yaml 無關）---${NC}"
            if [[ -f /etc/nginx/sites-available/vrops-alert-caller ]]; then
                log_info "偵測到既有 nginx 設定，保留不變"
                NGINX_ENABLED="true"
                NGINX_DOMAIN=$(grep 'server_name' /etc/nginx/sites-available/vrops-alert-caller \
                               2>/dev/null | awk '{print $2}' | tr -d ';' | head -1)
                NGINX_DOMAIN="${NGINX_DOMAIN:-}"
                CERTBOT_EMAIL=""
            else
                read -r -p "安裝 nginx 並啟用 HTTPS？[Y/n]: " _NGINX_OPT
                if [[ ! "${_NGINX_OPT:-Y}" =~ ^[Nn]$ ]]; then
                    NGINX_ENABLED="true"
                    read -r -p "伺服器域名（留空稍後在 WebGUI 設定）: " NGINX_DOMAIN
                    NGINX_DOMAIN="${NGINX_DOMAIN:-}"
                    if [[ -n "$NGINX_DOMAIN" ]]; then
                        read -r -p "Let's Encrypt Email: " CERTBOT_EMAIL
                        CERTBOT_EMAIL="${CERTBOT_EMAIL:-}"
                    else
                        CERTBOT_EMAIL=""
                    fi
                else
                    NGINX_ENABLED="false"
                    NGINX_DOMAIN=""
                    CERTBOT_EMAIL=""
                fi
            fi
            return 0
        fi
        log_info "→ 將重新設定（既有設定將在寫入前備份）"
    fi

    echo -e "\n${YELLOW}請輸入以下設定（Enter 使用預設值）${NC}\n"

    # ────────────────────────────────────────────────
    # 步驟 1：選擇外撥後端（SIP / Twilio，二選一）
    # ────────────────────────────────────────────────
    echo -e "${BLUE}--- 外撥後端選擇 ---${NC}"
    echo "  系統支援兩種外撥後端（二選一，安裝後可在 WebGUI 隨時切換）："
    echo "  1) SIP — 透過標準 SIP Trunk 撥號（EZUC+、Twilio SIP、FreePBX 等）"
    echo "  2) Twilio — 透過 Twilio REST API 外撥（需公開 HTTPS URL）"
    read -r -p "選擇外撥後端 [1]: " BACKEND_CHOICE
    case "${BACKEND_CHOICE:-1}" in
        2) CALL_BACKEND="twilio" ;;
        *) CALL_BACKEND="sip" ;;
    esac
    echo ""

    # ────────────────────────────────────────────────
    # 步驟 2：SIP 設定（若選 SIP 後端則必填；選 Twilio 後端可選填備用）
    # ────────────────────────────────────────────────
    if [[ "$CALL_BACKEND" == "sip" ]]; then
        echo -e "${BLUE}--- SIP 撥號設定 ---${NC}"
        echo "  支援任何標準 SIP Trunk（EZUC+、Twilio SIP Trunk、FreePBX、Asterisk 等）"

        read -r -p "SIP 伺服器（如 sip.example.com）: " SIP_SERVER
        while [[ -z "$SIP_SERVER" ]]; do
            log_warn "SIP 伺服器不能為空"
            read -r -p "SIP 伺服器: " SIP_SERVER
        done

        read -r -p "SIP Port [5061]: " SIP_PORT
        SIP_PORT="${SIP_PORT:-5061}"

        read -r -p "傳輸協定（tls/udp/tcp）[tls]: " SIP_TRANSPORT
        SIP_TRANSPORT="${SIP_TRANSPORT:-tls}"

        read -r -p "SIP 帳號（Username）: " SIP_USER
        while [[ -z "$SIP_USER" ]]; do
            log_warn "SIP 帳號不能為空"
            read -r -p "SIP 帳號: " SIP_USER
        done

        read -r -s -p "SIP 密碼: " SIP_PASS; echo
        while [[ -z "$SIP_PASS" ]]; do
            log_warn "SIP 密碼不能為空"
            read -r -s -p "SIP 密碼: " SIP_PASS; echo
        done

        TWILIO_ENABLED="false"
        TWILIO_ACCOUNT_SID="CHANGE_ME_ACCOUNT_SID"
        TWILIO_AUTH_TOKEN="CHANGE_ME_AUTH_TOKEN"
        TWILIO_FROM="+1XXXXXXXXXX"
        TWILIO_BASE_URL="https://your-server.example.com"

    else
        # Twilio 後端 — SIP 設定填入預設佔位值
        SIP_SERVER="sip.example.com"
        SIP_PORT="5061"
        SIP_TRANSPORT="tls"
        SIP_USER="CHANGE_ME_SIP_USER"
        SIP_PASS="CHANGE_ME_SIP_PASS"

        echo -e "${BLUE}--- Twilio 外撥設定 ---${NC}"
        read -r -p "Twilio Account SID: " TWILIO_ACCOUNT_SID
        while [[ -z "$TWILIO_ACCOUNT_SID" ]]; do
            log_warn "Account SID 不能為空"
            read -r -p "Twilio Account SID: " TWILIO_ACCOUNT_SID
        done

        read -r -s -p "Twilio Auth Token: " TWILIO_AUTH_TOKEN; echo
        while [[ -z "$TWILIO_AUTH_TOKEN" ]]; do
            log_warn "Auth Token 不能為空"
            read -r -s -p "Twilio Auth Token: " TWILIO_AUTH_TOKEN; echo
        done

        read -r -p "Twilio 發話號碼（E.164，如 +886912345678）: " TWILIO_FROM
        while [[ -z "$TWILIO_FROM" ]]; do
            log_warn "發話號碼不能為空"
            read -r -p "Twilio 發話號碼: " TWILIO_FROM
        done

        read -r -p "本服務公開 HTTPS URL（留空稍後設定，如 https://vrops.myddns.me）: " TWILIO_BASE_URL
        TWILIO_BASE_URL="${TWILIO_BASE_URL:-https://your-server.example.com}"
        TWILIO_BASE_URL="${TWILIO_BASE_URL%/}"

        TWILIO_ENABLED="true"
    fi

    # ────────────────────────────────────────────────
    # Webhook Token + Port
    # ────────────────────────────────────────────────
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

    # nginx + SSL 設定
    echo -e "\n${BLUE}--- nginx 反向代理 + HTTPS / Let's Encrypt ---${NC}"
    echo "  建議啟用，讓 Twilio 能透過 HTTPS 回呼本服務"
    echo "  需搭配 No-IP / DDNS 域名 + 路由器 Port 80/443 NAT 轉發"
    read -r -p "安裝 nginx 並啟用 HTTPS？[Y/n]: " NGINX_ENABLE_INPUT
    if [[ ! "$NGINX_ENABLE_INPUT" =~ ^[Nn]$ ]]; then
        NGINX_ENABLED="true"
        read -r -p "伺服器域名（如 vrops.myddns.me，留空稍後在 WebGUI 設定）: " NGINX_DOMAIN
        NGINX_DOMAIN="${NGINX_DOMAIN:-}"
        if [[ -n "$NGINX_DOMAIN" ]]; then
            read -r -p "Let's Encrypt 通知 Email（申請憑證用）: " CERTBOT_EMAIL
            CERTBOT_EMAIL="${CERTBOT_EMAIL:-}"
            # 若已提供 Twilio 且 base_url 未設定，自動填入
            if [[ "$TWILIO_ENABLED" == "true" && "$TWILIO_BASE_URL" == "https://your-server.example.com" ]]; then
                TWILIO_BASE_URL="https://$NGINX_DOMAIN"
            fi
        else
            CERTBOT_EMAIL=""
        fi
    else
        NGINX_ENABLED="false"
        NGINX_DOMAIN=""
        CERTBOT_EMAIL=""
    fi

    # 確認設定
    echo -e "\n${CYAN}═══════ 設定確認 ═══════${NC}"
    echo "  外撥後端:      $CALL_BACKEND"
    if [[ "$CALL_BACKEND" == "sip" ]]; then
        echo "  SIP 伺服器:    $SIP_SERVER:$SIP_PORT ($SIP_TRANSPORT)"
        echo "  SIP 帳號:      $SIP_USER"
    else
        echo "  Twilio SID:    ${TWILIO_ACCOUNT_SID:0:8}..."
        echo "  Twilio 號碼:   $TWILIO_FROM"
    fi
    echo "  Webhook Port:  $WEBHOOK_PORT"
    echo "  TTS 引擎:      $TTS_ENGINE"
    echo "  管理員帳號:    $ADMIN_USER"
    echo "  nginx HTTPS:   $NGINX_ENABLED"
    [[ -n "${NGINX_DOMAIN:-}" ]] && echo "  域名:          $NGINX_DOMAIN"
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
    # 若旗標指示保留現有設定，則完全跳過寫入
    if [[ "${_WRITE_SETTINGS:-true}" != "true" ]]; then
        log_info "已保留現有 settings.yaml（不覆蓋）"
        return 0
    fi

    # 覆蓋前先備份既有設定
    if [[ -f "$INSTALL_DIR/config/settings.yaml" ]]; then
        local _bak="/tmp/vrops-settings.yaml.bak.$(date +%Y%m%d_%H%M%S)"
        cp "$INSTALL_DIR/config/settings.yaml" "$_bak"
        log_info "既有設定已備份至：$_bak"
    fi

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
  transport: "${SIP_TRANSPORT:-tls}"
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

twilio:
  enabled: $TWILIO_ENABLED
  account_sid: "$TWILIO_ACCOUNT_SID"
  auth_token: "$TWILIO_AUTH_TOKEN"
  from_number: "$TWILIO_FROM"
  public_base_url: "$TWILIO_BASE_URL"

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
        ufw \
        nginx \
        certbot

    # 停用 nginx 預設站台（稍後由 setup_nginx 設定）
    rm -f /etc/nginx/sites-enabled/default
    mkdir -p /var/www/certbot
    systemctl enable nginx 2>/dev/null || true

    log_info "系統依賴安裝完成（含 nginx + certbot）"
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
    cd "$PJPROJECT_DIR/pjsip-apps/src/swig/python"
    python3 setup.py build 2>&1 | tail -5
    python3 setup.py install 2>&1 | tail -5
    ldconfig

    # setup.py install 有時不會更新 ldconfig，手動複製 .so 確保可見
    SO_FILE=$(find "$PJPROJECT_DIR" -name "_pjsua2*.so" 2>/dev/null | head -1)
    if [[ -n "$SO_FILE" ]]; then
        SYSPY_SITE=$(python3 -c "import site; print(site.getsitepackages()[0])" 2>/dev/null)
        if [[ -n "$SYSPY_SITE" ]]; then
            cp "$SO_FILE" "$SYSPY_SITE/" && log_info "手動複製 _pjsua2.so → $SYSPY_SITE"
            cp "$PJPROJECT_DIR/pjsip-apps/src/swig/python/pjsua2.py" "$SYSPY_SITE/"
        fi
    fi
    ldconfig

    # 驗證（cd ~ 避免抓到本地 pjsua2.py）
    cd ~
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

    # 從 GitHub clone（或若已存在則 pull 更新）
    if [[ -d "$INSTALL_DIR/.git" ]]; then
        log_info "偵測到既有 git repo，執行 git pull..."
        git config --global --add safe.directory "$INSTALL_DIR" 2>/dev/null || true
        git -C "$INSTALL_DIR" pull origin main
    else
        log_info "從 GitHub clone 程式碼..."
        # 若目錄已存在但不是 git repo（舊版 cp 安裝），先備份 config
        if [[ -d "$INSTALL_DIR" ]]; then
            if [[ -f "$INSTALL_DIR/config/settings.yaml" ]]; then
                cp "$INSTALL_DIR/config/settings.yaml" /tmp/vrops-settings.yaml.bak
                log_info "設定檔已備份至 /tmp/vrops-settings.yaml.bak"
            fi
            rm -rf "$INSTALL_DIR"
        fi
        git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
    fi

    # 建立不在 git repo 中的資料目錄
    mkdir -p "$INSTALL_DIR"/{logs,audio}

    # 還原備份的設定檔（若有）
    if [[ -f /tmp/vrops-settings.yaml.bak && ! -f "$INSTALL_DIR/config/settings.yaml" ]]; then
        mkdir -p "$INSTALL_DIR/config"
        cp /tmp/vrops-settings.yaml.bak "$INSTALL_DIR/config/settings.yaml"
        log_info "已還原備份的設定檔"
    fi

    # 設定權限
    chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
    chmod 755 "$INSTALL_DIR"
    chmod 700 "$INSTALL_DIR/logs"
    chmod 750 "$INSTALL_DIR/audio"

    log_info "目錄結構建立完成：$INSTALL_DIR"
}

# ────────────────────────────────────────────────
# Step 3b: nginx 反向代理 + SSL 設定
# ────────────────────────────────────────────────
setup_nginx() {
    log_section "Step 3b：設定 nginx 反向代理 + SSL"

    # 預設值（未從 collect_config 取得時的後備）
    NGINX_ENABLED="${NGINX_ENABLED:-true}"
    NGINX_DOMAIN="${NGINX_DOMAIN:-}"
    CERTBOT_EMAIL="${CERTBOT_EMAIL:-}"
    WEBHOOK_PORT="${WEBHOOK_PORT:-5000}"

    # ── 安裝 ssl_helper.sh sudoers 規則 ──────────────
    log_info "設定 sudoers（允許 $SERVICE_USER 執行 ssl_helper.sh）..."
    chmod 700 "$INSTALL_DIR/ssl_helper.sh"
    chown root:root "$INSTALL_DIR/ssl_helper.sh"

    cat > "/etc/sudoers.d/vrops-alert-ssl" <<EOF
# vROps Alert AutoCaller — SSL helper 授權（僅允許執行特定腳本）
$SERVICE_USER ALL=(root) NOPASSWD: $INSTALL_DIR/ssl_helper.sh
EOF
    chmod 440 "/etc/sudoers.d/vrops-alert-ssl"
    log_info "sudoers 規則已設定"

    # ── ssl/ 目錄（存放自訂憑證）─────────────────
    mkdir -p "$INSTALL_DIR/ssl"
    chown "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR/ssl"
    chmod 750 "$INSTALL_DIR/ssl"

    # ── 防火牆開放 80 / 443 ──────────────────────
    if command -v ufw &>/dev/null; then
        ufw allow 80/tcp  comment "HTTP (Let's Encrypt)" 2>/dev/null || true
        ufw allow 443/tcp comment "HTTPS"                2>/dev/null || true
    fi

    # ── 若安裝時已提供域名，立即設定 nginx + 申請憑證 ──
    if [[ "${NGINX_ENABLED:-false}" == "true" && -n "${NGINX_DOMAIN:-}" ]]; then

        log_info "設定 nginx 基礎設定（HTTP 先啟動）..."
        cat > "/etc/nginx/sites-available/vrops-alert-caller" <<NGINXCONF
# vROps Alert AutoCaller — 安裝時自動產生
server {
    listen 80;
    server_name $NGINX_DOMAIN;

    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { proxy_pass http://127.0.0.1:$WEBHOOK_PORT; }
}
NGINXCONF
        ln -sf /etc/nginx/sites-available/vrops-alert-caller \
               /etc/nginx/sites-enabled/vrops-alert-caller
        nginx -t && systemctl reload nginx || log_warn "nginx 重載失敗，請手動檢查"

        # 申請 Let's Encrypt 憑證（若提供了 email）
        if [[ -n "${CERTBOT_EMAIL:-}" ]]; then
            log_info "申請 Let's Encrypt 憑證（域名：$NGINX_DOMAIN）..."
            log_warn "nginx 將暫停約 10 秒進行 ACME 驗證..."

            systemctl stop nginx 2>/dev/null || true
            if certbot certonly \
                    --standalone \
                    --non-interactive \
                    --agree-tos \
                    --email "$CERTBOT_EMAIL" \
                    -d "$NGINX_DOMAIN" 2>&1; then
                log_info "憑證申請成功"
                CERT_PATH="/etc/letsencrypt/live/$NGINX_DOMAIN/fullchain.pem"
                KEY_PATH="/etc/letsencrypt/live/$NGINX_DOMAIN/privkey.pem"

                # 更新 nginx 為 HTTPS 設定
                cat > "/etc/nginx/sites-available/vrops-alert-caller" <<NGINXHTTPS
# vROps Alert AutoCaller — HTTPS 設定（自動產生）
server {
    listen 80;
    server_name $NGINX_DOMAIN;
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { return 301 https://\$host\$request_uri; }
}

server {
    listen 443 ssl;
    server_name $NGINX_DOMAIN;

    ssl_certificate     $CERT_PATH;
    ssl_certificate_key $KEY_PATH;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    proxy_set_header Host              \$host;
    proxy_set_header X-Real-IP         \$remote_addr;
    proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;
    proxy_set_header X-Forwarded-Host  \$host;

    location / {
        proxy_pass         http://127.0.0.1:$WEBHOOK_PORT;
        proxy_read_timeout 120;
        client_max_body_size 20M;
    }
}
NGINXHTTPS
                systemctl start nginx 2>/dev/null || true
                nginx -t && systemctl reload nginx
                log_info "nginx HTTPS 設定完成"
            else
                log_warn "Let's Encrypt 申請失敗（DNS/網路問題），稍後可在 WebGUI 重試"
                systemctl start nginx 2>/dev/null || true
            fi
        else
            log_info "未提供 Email，跳過 Let's Encrypt 申請（稍後可在 WebGUI 操作）"
            systemctl start nginx 2>/dev/null || true
        fi
    else
        # 無域名 — 設定一個預設 HTTP proxy 站台
        log_info "未設定域名，安裝 nginx 預設 HTTP proxy 設定..."
        cat > "/etc/nginx/sites-available/vrops-alert-caller" <<NGINXDEFAULT
# vROps Alert AutoCaller — 預設 HTTP proxy（無域名）
server {
    listen 80 default_server;
    location / { proxy_pass http://127.0.0.1:$WEBHOOK_PORT; }
}
NGINXDEFAULT
        ln -sf /etc/nginx/sites-available/vrops-alert-caller \
               /etc/nginx/sites-enabled/vrops-alert-caller
        nginx -t && systemctl reload nginx || log_warn "nginx 重載失敗"
        log_info "nginx 已設定為 HTTP proxy（Port 80 → $WEBHOOK_PORT）"
        log_info "之後可在 WebGUI 設定頁申請 Let's Encrypt 或上傳自訂 SSL 憑證"
    fi

    log_info "nginx / SSL 設定完成"
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
        # --system-site-packages 讓 venv 繼承系統安裝的 pjsua2.so
        # （pjsua2 由原始碼編譯安裝到 /usr/local/lib，pip 無法安裝）
        sudo -u "$SERVICE_USER" "$PYTHON_BIN" -m venv --system-site-packages "$VENV_DIR"
        log_info "虛擬環境建立（含 system-site-packages）：$VENV_DIR"
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
        "pyttsx3>=2.90" \
        "twilio>=8.0.0"

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

    # nginx / SSL（sudoers + 目錄）
    setup_nginx

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
    local LOCAL_IP
    LOCAL_IP=$(hostname -I | awk '{print $1}')

    echo ""
    echo -e "${GREEN}╔══════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║   vROps Alert AutoCaller 安裝完成！                 ║${NC}"
    echo -e "${GREEN}╚══════════════════════════════════════════════════════╝${NC}"
    echo ""

    if [[ "${NGINX_ENABLED:-false}" == "true" && -n "${NGINX_DOMAIN:-}" ]]; then
        echo -e "  ${CYAN}Webhook URL：${NC}  https://$NGINX_DOMAIN/vrops-webhook"
        echo -e "  ${CYAN}WebGUI URL：${NC}   https://$NGINX_DOMAIN"
        echo -e "  ${CYAN}（內部直連）：${NC} http://$LOCAL_IP:$WEBHOOK_PORT"
    else
        echo -e "  ${CYAN}Webhook URL：${NC}  http://$LOCAL_IP:$WEBHOOK_PORT/vrops-webhook"
        echo -e "  ${CYAN}WebGUI URL：${NC}   http://$LOCAL_IP:$WEBHOOK_PORT"
    fi
    echo -e "  ${CYAN}管理員帳號：${NC}   $ADMIN_USER"
    echo -e "  ${CYAN}健康檢查：${NC}     http://localhost:$WEBHOOK_PORT/health"
    echo ""
    echo -e "  ${YELLOW}下一步：${NC}"
    echo "  1. 在 vROps 設定 Webhook URL 和 Authorization: Bearer $WEBHOOK_TOKEN"
    echo "  2. 開啟 WebGUI 新增聯絡人和路由規則"
    if [[ "${NGINX_ENABLED:-false}" == "true" && -z "${NGINX_DOMAIN:-}" ]]; then
        echo "  3. 到 WebGUI 設定頁申請 Let's Encrypt 憑證（SSL 設定卡片）"
    fi
    echo "  3. 執行冒煙測試：bash $SCRIPT_DIR/tests/test_smoke.sh"
    echo ""
    echo -e "  ${CYAN}常用指令：${NC}"
    echo "  systemctl status $SERVICE_NAME         # 查看服務狀態"
    echo "  journalctl -u $SERVICE_NAME -f         # 即時日誌"
    echo "  systemctl status nginx                  # nginx 狀態"
    echo "  sudo bash $INSTALL_DIR/update.sh       # 一鍵更新"
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
            --update)     MODE="update" ;;
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
        update)
            check_root
            log_section "從 GitHub 更新程式碼"
            if [[ ! -d "$INSTALL_DIR/.git" ]]; then
                log_error "$INSTALL_DIR 不是 git repo，請先完整安裝後再使用 --update"
                exit 1
            fi
            # 保留 settings.yaml（在 .gitignore，git pull 不會觸碰）
            git config --global --add safe.directory "$INSTALL_DIR" 2>/dev/null || true
            git -C "$INSTALL_DIR" pull origin main
            # 修正 ssl_helper.sh 權限（git pull 後 owner 可能變 root）
            if [[ -f "$INSTALL_DIR/ssl_helper.sh" ]]; then
                chown root:root "$INSTALL_DIR/ssl_helper.sh"
                chmod 700 "$INSTALL_DIR/ssl_helper.sh"
            fi
            # 修正 config/ 權限
            chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR/config/" 2>/dev/null || true
            chmod 640 "$INSTALL_DIR/config/settings.yaml" 2>/dev/null || true
            # 更新 Python 套件
            if [[ -f "$INSTALL_DIR/venv/bin/pip" ]]; then
                log_info "更新 Python 套件..."
                sudo -u "$SERVICE_USER" "$INSTALL_DIR/venv/bin/pip" install -q \
                    -r "$INSTALL_DIR/requirements.txt" 2>/dev/null || \
                    log_warn "pip 套件更新失敗（非致命）"
            fi
            systemctl restart "$SERVICE_NAME"
            log_info "更新完成，服務已重啟"
            systemctl status "$SERVICE_NAME" --no-pager | head -5
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
