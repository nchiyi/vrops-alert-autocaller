# Agent B — 核心引擎

## 職責

開發三個核心 Python 模組：TTS 語音合成、SIP 撥號、告警管理。

## 交付物

| 檔案 | 說明 |
|------|------|
| `tts_engine.py` | 文字轉語音（edge-tts / gTTS / pyttsx3 三層降級） |
| `sip_caller.py` | SIP TLS 撥號（pjsua2 單例模式，防 GC 靜音） |
| `alert_manager.py` | 去重 / 重撥 / 升級 / SQLite 日誌 |
| `audio/` | 備援語音存放目錄（`fallback_alert.wav`） |
| `tests/` | 單元測試腳本 |

## 部署位置

```
/opt/vrops-alert-caller/
├── tts_engine.py
├── sip_caller.py
├── alert_manager.py
└── audio/
    └── fallback_alert.wav   ← 安裝後執行: python tts_engine.py --generate-fallback
```

## 快速開始

### 1. 安裝 Python 依賴

```bash
pip install -r requirements.txt
```

### 2. 安裝系統依賴

```bash
# 音訊轉換（必須）
apt install -y ffmpeg

# 離線 TTS 備援（可選）
apt install -y espeak-ng
```

### 3. 編譯安裝 pjsua2

pjsua2 無法透過 pip 安裝，需從原始碼編譯：

```bash
cd /tmp
git clone --depth 1 https://github.com/pjsip/pjproject.git
cd pjproject
./configure --enable-shared --with-ssl=/usr CFLAGS="-fPIC"
make -j$(nproc) && make install
cd pjsip-apps/src/swig/python && make && make install
ldconfig

# 驗證
python3 -c "import pjsua2; print('pjsua2 OK')"
```

### 4. 產生備援語音

```bash
python tts_engine.py --generate-fallback
```

### 5. 執行單元測試

```bash
# 測試 TTS 模組
python -m pytest tests/test_tts_engine.py -v

# 測試告警管理（不需 pjsua2）
python -m pytest tests/test_alert_manager.py -v

# 測試 SIP 模組（使用 Mock）
python -m pytest tests/test_sip_caller.py -v
```

## 函式簽章（介面契約）

Agent B 必須嚴格遵守以下函式簽章，不可自行修改：

| 模組 | 函式 | 簽章 |
|------|------|------|
| `tts_engine` | `synthesize_speech` | `(text: str, config: dict) → str` |
| `sip_caller` | `make_sip_call` | `(wav_path: str, target_number: str, config: dict) → CallReport` |
| `alert_manager` | `is_duplicate` | `(alert_key: str) → bool` |
| `alert_manager` | `call_with_escalation` | `(wav_path, targets, alert_data, routed_group="")` |

## call_log 表結構

**不可增減欄位**（必須與 Agent C 的 `web/models.py` 完全一致）：

```sql
CREATE TABLE IF NOT EXISTS call_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    alert_key TEXT NOT NULL,
    alert_name TEXT,
    resource_name TEXT,
    criticality TEXT,
    target_name TEXT NOT NULL,
    target_number TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    result TEXT NOT NULL,
    duration_seconds REAL,
    error_message TEXT,
    routed_group TEXT DEFAULT ''
);
```

## 已修正問題（參考 10-審查與修正報告）

| 問題 | 嚴重度 | 修正方案 |
|------|--------|---------|
| pjsua2 每次撥號重建 Endpoint | 🔴 致命 | SipEngine 單例模式 |
| AudioMediaPlayer 被 GC 回收導致靜音 | 🔴 致命 | 存為 `self._player` 實例變數 |
| edge-tts asyncio.run() 在子線程崩潰 | 🟡 嚴重 | 使用 `asyncio.new_event_loop()` |
| 去重快取重啟失效 | 🟠 中等 | 記憶體快取 + SQLite 雙層去重 |

## 相依關係

- **被 Agent D** 的 `webhook_server.py` 引入
- **呼叫** Agent C 的路由結果（targets 列表由 `routing_engine.resolve_targets()` 提供）
- **共用 DB** 與 Agent C 的 WebGUI（同一個 `alerts.db`，同一個 `call_log` 表）
