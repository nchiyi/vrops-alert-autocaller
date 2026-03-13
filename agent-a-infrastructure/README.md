# Agent A — 基礎設施交付物

**負責範圍**：Ubuntu VM 完整執行環境建置

---

## 目錄結構

```
agent-a-infrastructure/
├── README.md                        # 本文件
├── requirements.txt                 # Python 套件版本清單
├── main.py                          # 主程式入口（開發用直接執行）
├── config/
│   └── settings.yaml                # 統一設定檔（需填入 SIP 帳密）
├── scripts/
│   ├── 01-system-setup.sh           # 系統依賴安裝 + 目錄建立
│   ├── 02-install-pjsua2.sh         # pjsua2 編譯安裝（SIP TLS）
│   ├── 03-setup-venv.sh             # Python 虛擬環境 + 套件安裝
│   ├── 04-firewall.sh               # UFW 防火牆規則
│   ├── 05-deploy-service.sh         # systemd + logrotate + cron 安裝
│   └── 06-verify.sh                 # 環境驗證清單
├── systemd/
│   └── vrops-alert-caller.service   # systemd service 定義
├── docker/
│   ├── Dockerfile                   # Multi-stage 映像建置
│   └── docker-compose.yml           # Docker Compose 部署設定
└── logrotate/
    ├── vrops-alert-caller           # logrotate 規則（複製到 /etc/logrotate.d/）
    └── vrops-cron                   # cron jobs（複製到 /etc/cron.d/）
```

---

## 快速部署步驟

### 方式一：Systemd（推薦）

```bash
# 1. 系統基礎安裝
chmod +x scripts/*.sh
sudo ./scripts/01-system-setup.sh

# 2. 編譯 pjsua2（約 10-20 分鐘）
sudo ./scripts/02-install-pjsua2.sh

# 3. 建立 Python 虛擬環境
sudo ./scripts/03-setup-venv.sh

# 4. 設定防火牆
sudo ./scripts/04-firewall.sh

# 5. 填入 SIP 帳密
nano config/settings.yaml
# 修改 YOUR_SIP_USERNAME / YOUR_SIP_PASSWORD / YOUR_WEBHOOK_SECRET

# 6. 複製設定檔到部署目錄
sudo cp config/settings.yaml /opt/vrops-alert-caller/config/

# 7. 等 Agent B/C/D 程式碼就位後，部署 service
sudo ./scripts/05-deploy-service.sh
sudo systemctl enable --now vrops-alert-caller

# 8. 驗證
./scripts/06-verify.sh
sudo systemctl status vrops-alert-caller
```

### 方式二：Docker Compose

```bash
# 1. 填入 SIP 帳密
nano config/settings.yaml

# 2. 建置並啟動
cd docker
docker compose build
docker compose up -d
docker compose logs -f
```

---

## 重要注意事項

| 項目 | 說明 |
|------|------|
| workers=1 threads=4 | 確保去重快取與 SipEngine 單例在同一 Process；多 worker 會導致狀態不共享 |
| SIP TLS | 需要 `libssl-dev` 在 pjsua2 編譯前已安裝，否則 TLS 支援被靜默跳過 |
| network_mode: host | Docker 部署時 SIP RTP 串流需要 host network 避免 NAT 問題 |
| settings.yaml 權限 | 含帳密，務必設定 `chmod 640` 且由 `vrops-alert` 擁有 |

---

## 交付驗收標準

- [ ] `python3 -c "import pjsua2; print('OK')"` 輸出 OK
- [ ] `/opt/vrops-alert-caller/` 目錄結構完整
- [ ] `systemctl status vrops-alert-caller` 顯示 loaded（尚不需 active）
- [ ] `ufw status` 顯示 port 5000/tcp 開放
- [ ] `06-verify.sh` 全部通過

---

## 對其他 Agent 的依賴說明

| Agent | 依賴 | 說明 |
|-------|------|------|
| Agent B | 無 | 此線路可最先獨立開始 |
| Agent C | 無 | 此線路可最先獨立開始 |
| Agent D | 需 Agent A 完成 | D 開始整合前需環境就緒 |

**此線路無外部依賴，可立即執行。**
