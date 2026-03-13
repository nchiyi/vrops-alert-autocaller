# vROps Alert AutoCaller

當 VMware vROps 偵測到 VM 異常時，自動撥打電話給值班人員並以中文語音播報告警。

## 快速開始

```bash
git clone https://github.com/nchiyi/vrops-alert-autocaller.git
cd vrops-alert-autocaller/vrops-alert-caller

# 方法一：Ubuntu 原生安裝（互動式設定）
sudo bash install.sh

# 方法二：Docker 安裝
bash install.sh --docker
```

安裝腳本會自動完成：
- 系統依賴安裝（ffmpeg、espeak-ng 等）
- pjsua2 編譯（SIP TLS 支援）
- Python 虛擬環境建立
- 互動式設定收集（SIP 帳密、WebGUI 密碼）
- systemd 服務部署
- 防火牆規則設定
- 備援語音產生

**使用者只需填入環境參數（SIP 帳號密碼、管理員密碼），其餘全自動完成。**

## 架構

```
vROps → Webhook → Flask → Queue → TTS → SIP(pjsua2) → EZUC+ → 值班人員手機
                                    ↓
                              路由引擎（依 VM 名稱分流）
                                    ↓
                              WebGUI（聯絡人/路由規則管理）
```

## 設定說明

安裝完成後，設定檔位於 `/opt/vrops-alert-caller/config/settings.yaml`。
修改設定後執行：`systemctl restart vrops-alert-caller`

## vROps Webhook 設定

在 vROps 的 Alert Notification 設定：
- URL：`http://YOUR_SERVER_IP:5000/vrops-webhook`
- Method：`POST`
- Header：`Authorization: Bearer YOUR_WEBHOOK_TOKEN`（Token 安裝時自動產生）

## 移除

```bash
sudo bash install.sh --uninstall
```
