#!/usr/bin/env bash
# =============================================================
# vROps Alert SSL Helper — 以 root 執行，透過 sudoers 授權
# 用法：sudo /opt/vrops-alert-caller/ssl_helper.sh <action> [args...]
#
# Actions:
#   install-nginx                  — 安裝 nginx + certbot（首次）
#   certbot <domain> <email>       — 申請 Let's Encrypt 憑證
#   apply-nginx <domain> <port>    — 產生 nginx HTTPS 設定並 reload
#   apply-custom <domain> <port>   — 套用手動上傳的憑證並 reload nginx
#   cert-status <domain>           — 回傳憑證到期資訊
#   nginx-status                   — 回傳 nginx 運作狀態
# =============================================================
set -euo pipefail

INSTALL_DIR="/opt/vrops-alert-caller"
SSL_DIR="$INSTALL_DIR/ssl"
NGINX_CONF="/etc/nginx/sites-available/vrops-alert-caller"
NGINX_LINK="/etc/nginx/sites-enabled/vrops-alert-caller"
WEBROOT="/var/www/certbot"

action="${1:-}"
shift || true

case "$action" in

    install-nginx)
        # ── 安裝 nginx + certbot（若尚未安裝）──────────────
        apt-get install -y -q nginx certbot 2>&1 | grep -v "^$" | grep -v "^Reading\|^Building\|^Unpacking\|^Setting\|^Processing" || true
        mkdir -p "$WEBROOT"
        # 停用預設站台
        rm -f /etc/nginx/sites-enabled/default
        # 確保 nginx 啟動
        systemctl enable nginx 2>/dev/null || true
        systemctl start nginx 2>/dev/null || systemctl restart nginx 2>/dev/null || true
        # 開放防火牆
        ufw allow 80/tcp  comment "HTTP (Let's Encrypt)" 2>/dev/null || true
        ufw allow 443/tcp comment "HTTPS"                2>/dev/null || true
        echo "INSTALL_OK"
        ;;

    certbot)
        # ── 申請 Let's Encrypt 憑證 ───────────────────────
        domain="${1:?domain required}"
        email="${2:?email required}"

        # 暫停 nginx，讓 certbot standalone 使用 port 80
        systemctl stop nginx 2>/dev/null || true

        certbot certonly \
            --standalone \
            --non-interactive \
            --agree-tos \
            --email "$email" \
            -d "$domain" \
            2>&1 || { systemctl start nginx 2>/dev/null || true; echo "CERTBOT_FAIL"; exit 1; }

        systemctl start nginx 2>/dev/null || true
        echo "CERTBOT_OK"
        ;;

    apply-nginx)
        # ── 產生 nginx 設定（Let's Encrypt 憑證路徑）────────
        domain="${1:?domain required}"
        app_port="${2:-5000}"

        CERT_PATH="/etc/letsencrypt/live/$domain/fullchain.pem"
        KEY_PATH="/etc/letsencrypt/live/$domain/privkey.pem"

        # 若 LE 憑證不存在，改用 ssl/ 自訂憑證
        if [[ ! -f "$CERT_PATH" ]]; then
            CERT_PATH="$SSL_DIR/fullchain.pem"
            KEY_PATH="$SSL_DIR/privkey.pem"
        fi

        if [[ ! -f "$CERT_PATH" || ! -f "$KEY_PATH" ]]; then
            echo "CERT_NOT_FOUND"
            exit 1
        fi

        cat > "$NGINX_CONF" <<NGINX_CONF
# vROps Alert AutoCaller — 自動產生，勿手動修改
# 更新時間：$(date '+%Y-%m-%d %H:%M:%S')

server {
    listen 80;
    server_name $domain;

    # Let's Encrypt 驗證路徑
    location /.well-known/acme-challenge/ {
        root $WEBROOT;
    }

    # 其餘一律轉 HTTPS
    location / {
        return 301 https://\$host\$request_uri;
    }
}

server {
    listen 443 ssl;
    server_name $domain;

    ssl_certificate     $CERT_PATH;
    ssl_certificate_key $KEY_PATH;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    # 傳遞真實來源資訊給 Flask
    proxy_set_header Host              \$host;
    proxy_set_header X-Real-IP         \$remote_addr;
    proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;
    proxy_set_header X-Forwarded-Host  \$host;

    location / {
        proxy_pass         http://127.0.0.1:$app_port;
        proxy_read_timeout 120;
        client_max_body_size 20M;
    }
}
NGINX_CONF

        ln -sf "$NGINX_CONF" "$NGINX_LINK"
        nginx -t
        systemctl reload nginx
        echo "NGINX_OK"
        ;;

    apply-custom)
        # ── 套用自訂上傳憑證 ─────────────────────────────
        domain="${1:?domain required}"
        app_port="${2:-5000}"

        CERT_PATH="$SSL_DIR/fullchain.pem"
        KEY_PATH="$SSL_DIR/privkey.pem"

        if [[ ! -f "$CERT_PATH" || ! -f "$KEY_PATH" ]]; then
            echo "CERT_NOT_FOUND"
            exit 1
        fi

        cat > "$NGINX_CONF" <<NGINX_CONF
# vROps Alert AutoCaller — 自動產生，勿手動修改
# 更新時間：$(date '+%Y-%m-%d %H:%M:%S')

server {
    listen 80;
    server_name $domain;
    location / { return 301 https://\$host\$request_uri; }
}

server {
    listen 443 ssl;
    server_name $domain;

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
        proxy_pass         http://127.0.0.1:$app_port;
        proxy_read_timeout 120;
        client_max_body_size 20M;
    }
}
NGINX_CONF

        ln -sf "$NGINX_CONF" "$NGINX_LINK"
        nginx -t
        systemctl reload nginx
        echo "CUSTOM_OK"
        ;;

    cert-status)
        # ── 查詢憑證到期日 ─────────────────────────────
        domain="${1:-}"

        # 優先查 Let's Encrypt
        if [[ -n "$domain" && -f "/etc/letsencrypt/live/$domain/cert.pem" ]]; then
            expiry=$(openssl x509 -enddate -noout \
                -in "/etc/letsencrypt/live/$domain/cert.pem" 2>/dev/null \
                | cut -d= -f2)
            echo "LE $expiry"
        elif [[ -f "$SSL_DIR/fullchain.pem" ]]; then
            expiry=$(openssl x509 -enddate -noout \
                -in "$SSL_DIR/fullchain.pem" 2>/dev/null \
                | cut -d= -f2)
            echo "CUSTOM $expiry"
        else
            echo "NONE"
        fi
        ;;

    nginx-status)
        # ── nginx 運作狀態 ─────────────────────────────
        if systemctl is-active --quiet nginx 2>/dev/null; then
            echo "RUNNING"
        else
            echo "STOPPED"
        fi
        ;;

    *)
        echo "Unknown action: $action" >&2
        exit 1
        ;;
esac
