#!/usr/bin/env python3
"""
webhook_server.py — vROps 告警 Webhook 接收模組 v3

v2 修正: Queue + 單一消費者線程
v3 優化: WebGUI 登入驗證 / Queue TTL / 告警風暴合併

Agent D 整合版：組裝所有模組並啟動服務。
"""

import os
import sys
import time
import logging
import queue
import threading
import atexit
import functools
from datetime import datetime
from flask import Flask, request, jsonify, session, redirect, url_for, render_template
import yaml

# 讓 Python 能找到同層或父層模組
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from tts_engine import synthesize_speech
from sip_caller import make_sip_call, SipEngine
from alert_manager import AlertManager
from routing_engine import resolve_targets
from web.routes import gui
from web.models import init_db

# ============================
# 載入設定
# ============================

CONFIG_PATH = os.path.join(BASE_DIR, "config", "settings.yaml")
with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

# ============================
# Flask App 初始化
# ============================

app = Flask(__name__,
            template_folder=os.path.join(BASE_DIR, "web", "templates"),
            static_folder=os.path.join(BASE_DIR, "web", "static"),
            static_url_path="/static")
app.secret_key = CONFIG.get("webgui", {}).get(
    "secret_key", os.urandom(24).hex()
)

# 初始化資料庫（建立 4 張表）
init_db()

# 初始化告警管理器
alert_mgr = AlertManager(CONFIG)

# 設定日誌
logging.basicConfig(
    level=getattr(logging, CONFIG["logging"]["level"]),
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(CONFIG["logging"]["file"], encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# 註冊 WebGUI Blueprint
app.register_blueprint(gui)


# ============================
# WebGUI 登入驗證
# ============================

WEBGUI_USERS = CONFIG.get("webgui", {}).get("users", {
    "admin": "changeme"   # 預設帳密，正式環境務必修改
})


def login_required(f):
    """裝飾器：WebGUI 頁面/API 需登入"""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            if request.is_json:
                return jsonify({"error": "unauthorized"}), 401
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated


@app.route("/login", methods=["GET", "POST"])
def login():
    """登入頁（GET 顯示表單、POST 驗證）"""
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if WEBGUI_USERS.get(username) == password:
            session["logged_in"] = True
            session["username"] = username
            logger.info(f"WebGUI 登入成功: {username}")
            return redirect("/")
        logger.warning(f"WebGUI 登入失敗: {username} from {request.remote_addr}")
        return render_template("login.html", error="帳號或密碼錯誤"), 401
    return render_template("login.html", error="")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


# ============================
# Alert Queue + TTL + 風暴合併
# ============================

# Queue 存放 (入隊時間戳, alert_data) 的 tuple
alert_queue: queue.Queue = queue.Queue()
_shutdown_event = threading.Event()

# 可設定參數
QUEUE_TTL_SECONDS = CONFIG.get("alert", {}).get("queue_ttl_seconds", 600)
BATCH_THRESHOLD = CONFIG.get("alert", {}).get("batch_threshold", 3)

BATCH_TEMPLATE_ZH = (
    "注意，這是 vROps 批次告警通知。"
    "系統目前有 {count} 筆待處理告警。"
    "其中緊急等級 {critical_count} 筆。"
    "受影響主機包含：{hosts}。"
    "請立即登入 vROps 儀表板查看詳細資訊。"
)


def alert_consumer():
    """
    單一消費者線程。v3 新增：
    - TTL 過期檢查（超過 10 分鐘的告警直接丟棄）
    - 告警風暴合併（Queue 積壓超過 BATCH_THRESHOLD 時合併撥號）
    """
    logger.info("告警消費者線程啟動")
    while not _shutdown_event.is_set():
        try:
            enqueue_time, data = alert_queue.get(timeout=1.0)
        except queue.Empty:
            continue

        try:
            # TTL 檢查
            age = time.time() - enqueue_time
            if age > QUEUE_TTL_SECONDS:
                logger.warning(
                    f"告警已過期 ({age:.0f}s > {QUEUE_TTL_SECONDS}s)，"
                    f"丟棄: {data.get('alertName')}"
                )
                continue

            # 風暴合併：看 Queue 裡還有多少積壓
            pending = alert_queue.qsize()
            if pending >= BATCH_THRESHOLD:
                # 把目前這筆 + Queue 中的全部取出，合併為一通電話
                batch = [data]
                while not alert_queue.empty():
                    try:
                        _, extra = alert_queue.get_nowait()
                        batch.append(extra)
                        alert_queue.task_done()
                    except queue.Empty:
                        break

                logger.info(
                    f"告警風暴偵測！合併 {len(batch)} 筆告警為一通電話"
                )
                process_batch_alert(batch)
            else:
                process_alert(data)

        except Exception as e:
            logger.error(f"處理告警失敗: {e}", exc_info=True)
        finally:
            alert_queue.task_done()

    logger.info("告警消費者線程結束")


_consumer_thread = threading.Thread(
    target=alert_consumer, name="alert-consumer"
)
_consumer_thread.daemon = True
_consumer_thread.start()


# ============================
# 語音文稿模板
# ============================

ALERT_TEMPLATE_ZH = (
    "注意，這是 vROps 告警通知。"
    "嚴重等級：{criticality}。"
    "主機名稱：{resource}。"
    "告警項目：{alert}。"
    "詳細資訊：{info}。"
    "請立即處理。"
    "重複一次，"
    "主機：{resource}，告警：{alert}。"
)

SEVERITY_MAP = {
    "CRITICAL": "緊急",
    "IMMEDIATE": "立即",
    "WARNING": "警告",
    "INFORMATION": "資訊",
}


def build_speech_text(data: dict) -> str:
    """從 vROps JSON 擷取關鍵欄位，套入固定模板。零失真。"""
    return ALERT_TEMPLATE_ZH.format(
        criticality=SEVERITY_MAP.get(
            data.get("criticality", ""), "未知"
        ),
        resource=data.get("resourceName", "未知主機"),
        alert=data.get("alertName", "未知告警"),
        info=data.get("info", "無詳細資訊")[:100],
    )


def process_alert(data: dict):
    """處理單筆告警：模板 → 路由 → TTS → 撥號"""
    try:
        speech_text = build_speech_text(data)
        logger.info(f"語音文稿: {speech_text}")

        wav_path = synthesize_speech(
            text=speech_text,
            config=CONFIG["tts"]
        )

        targets, rule_name = resolve_targets(data)
        if not targets:
            logger.error("路由引擎無可用聯絡人，跳過此告警")
            return

        logger.info(f"路由: {rule_name} → {[t['name'] for t in targets]}")

        alert_mgr.call_with_escalation(
            wav_path=wav_path,
            targets=targets,
            alert_data=data,
            routed_group=rule_name
        )

    except Exception as e:
        logger.error(f"處理告警失敗: {e}", exc_info=True)


def process_batch_alert(batch: list):
    """
    處理合併告警（告警風暴時觸發）。
    不逐筆播報，改為一通電話摘要通知。
    """
    try:
        critical_count = sum(
            1 for d in batch if d.get("criticality") == "CRITICAL"
        )
        hosts = list(set(
            d.get("resourceName", "?") for d in batch
        ))[:5]

        speech_text = BATCH_TEMPLATE_ZH.format(
            count=len(batch),
            critical_count=critical_count,
            hosts="、".join(hosts)
        )
        logger.info(f"批次語音文稿: {speech_text}")

        wav_path = synthesize_speech(
            text=speech_text,
            config=CONFIG["tts"]
        )

        targets, rule_name = resolve_targets(batch[0])
        if not targets:
            logger.error("路由引擎無可用聯絡人，跳過批次告警")
            return

        batch_alert_data = {
            "alertName": f"批次告警 ({len(batch)} 筆)",
            "resourceName": ", ".join(hosts),
            "criticality": "CRITICAL" if critical_count > 0 else "WARNING",
            "info": f"含 {critical_count} 筆緊急告警"
        }

        alert_mgr.call_with_escalation(
            wav_path=wav_path,
            targets=targets,
            alert_data=batch_alert_data,
            routed_group=rule_name
        )

    except Exception as e:
        logger.error(f"處理批次告警失敗: {e}", exc_info=True)


# ============================
# Flask API — Webhook（Token 驗證）
# ============================

@app.route("/vrops-webhook", methods=["POST"])
def vrops_webhook():
    """接收 vROps Webhook 推送（Token 驗證，不需 WebGUI 登入）"""
    auth_token = CONFIG["webhook"].get("auth_token")
    if auth_token and auth_token != "YOUR_WEBHOOK_SECRET":
        provided = request.headers.get("Authorization", "")
        if provided != f"Bearer {auth_token}":
            logger.warning(f"Webhook 認證失敗: {request.remote_addr}")
            return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "invalid json"}), 400

    logger.info(
        f"收到 vROps 告警: {data.get('alertName')} "
        f"({data.get('resourceName')})"
    )

    alert_key = f"{data.get('alertName')}_{data.get('resourceName')}"
    if alert_mgr.is_duplicate(alert_key):
        logger.info(f"重複告警已忽略: {alert_key}")
        return jsonify({"status": "duplicate_ignored"}), 200

    alert_queue.put((time.time(), data))
    logger.info(f"告警已加入佇列 (queue size={alert_queue.qsize()})")

    return jsonify({
        "status": "accepted",
        "alert": data.get("alertName"),
        "resource": data.get("resourceName"),
        "queue_size": alert_queue.qsize(),
        "timestamp": datetime.now().isoformat()
    }), 202


# ============================
# Flask API — 健康檢查（不需登入）
# ============================

@app.route("/health", methods=["GET"])
def health():
    try:
        sip_ready = SipEngine._instance is not None and SipEngine._instance.is_ready()
    except Exception:
        sip_ready = False

    return jsonify({
        "status": "ok",
        "service": "vROps Alert AutoCaller",
        "sip_registered": sip_ready,
        "queue_size": alert_queue.qsize(),
        "consumer_alive": _consumer_thread.is_alive()
    })


# ============================
# Flask API — 通話紀錄（需登入）
# ============================

@app.route("/alerts/history", methods=["GET"])
@login_required
def alert_history():
    return jsonify(alert_mgr.get_history(limit=50))


# ============================
# Graceful Shutdown
# ============================

def graceful_shutdown():
    logger.info("收到停止信號，等待 Queue 處理完畢...")
    _shutdown_event.set()
    _consumer_thread.join(timeout=30)
    if SipEngine._instance:
        SipEngine._instance.shutdown()
    logger.info("服務已優雅關閉")


atexit.register(graceful_shutdown)


# ============================
# 啟動
# ============================

if __name__ == "__main__":
    logger.info("=== vROps Alert AutoCaller v3 啟動 ===")
    app.run(
        host=CONFIG["webhook"]["host"],
        port=CONFIG["webhook"]["port"],
        debug=False
    )
