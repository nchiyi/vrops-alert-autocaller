#!/usr/bin/env python3
"""
routes.py — WebGUI 路由 + REST API
[v4] 儲存後自動重載服務、新增測試撥號 API、Twilio URL 偵測
"""

import os
import copy
import signal
import threading
import functools
import logging
import yaml
from flask import (
    Blueprint, render_template, request,
    jsonify, redirect, session
)
from web import models

logger = logging.getLogger(__name__)

# 設定檔路徑（web/ 的上一層目錄）
_here = os.path.dirname(os.path.abspath(__file__))
_BASE_DIR = os.path.dirname(_here)
SETTINGS_PATH = os.environ.get(
    "VROPS_SETTINGS_PATH",
    os.path.join(_BASE_DIR, "config", "settings.yaml")
)

# 遮罩值：前端顯示用，儲存時若值不變則保留原密碼
_MASK = "●●●●●●●●"

gui = Blueprint("gui", __name__,
                template_folder=os.path.join(_here, "templates"),
                static_folder=os.path.join(_here, "static"),
                static_url_path="/static")


# ============================
# 設定檔輔助函式
# ============================

def _ensure_settings_exists():
    """若 settings.yaml 不存在，從 settings.yaml.example 複製一份"""
    if os.path.exists(SETTINGS_PATH):
        return
    example_path = SETTINGS_PATH.replace("settings.yaml", "settings.yaml.example")
    if os.path.exists(example_path):
        import shutil
        shutil.copy2(example_path, SETTINGS_PATH)
        logger.info(f"已從範本建立設定檔：{SETTINGS_PATH}")
    else:
        logger.warning(f"找不到設定檔範本：{example_path}")


def _read_yaml() -> dict:
    """讀取 settings.yaml，不存在時從範本自動建立"""
    _ensure_settings_exists()
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _write_yaml(cfg: dict):
    """將 dict 寫回 settings.yaml"""
    # 先確認寫入權限
    if not os.access(SETTINGS_PATH, os.W_OK):
        raise PermissionError(
            f"無寫入權限：{SETTINGS_PATH}\n"
            f"請在伺服器執行：sudo chown vrops-alert:vrops-alert {SETTINGS_PATH}"
        )
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False,
                  sort_keys=False)


def _masked(cfg: dict) -> dict:
    """回傳遮罩後的設定（供前端顯示，不洩漏密碼）"""
    c = copy.deepcopy(cfg)
    sensitive_keys = {"password", "auth_token", "secret_key"}
    for section in c.values():
        if isinstance(section, dict):
            for k in sensitive_keys:
                if k in section and section[k]:
                    section[k] = _MASK
    return c


def _merge_settings(original: dict, updates: dict) -> dict:
    """
    合併更新值，若前端回傳 _MASK 則保留原始值（密碼未修改）。
    只允許已知安全的欄位被更新。
    """
    result = copy.deepcopy(original)

    ALLOWED = {
        "sip": {"server", "port", "transport", "username", "password"},
        "twilio": {"enabled", "account_sid", "auth_token",
                   "from_number", "public_base_url"},
        "webhook": {"auth_token"},
    }

    for section, fields in ALLOWED.items():
        if section not in updates:
            continue
        if section not in result:
            result[section] = {}
        for field in fields:
            if field not in updates[section]:
                continue
            new_val = updates[section][field]
            # 前端未修改的密碼欄位仍顯示遮罩 → 保留原始值
            if new_val == _MASK:
                continue
            # 型態轉換
            if field == "port":
                try:
                    new_val = int(new_val)
                except (ValueError, TypeError):
                    pass
            if field == "enabled":
                new_val = bool(new_val)
            result[section][field] = new_val

    return result


def _deferred_reload(delay: float = 2.0):
    """
    延遲後對 gunicorn master 送 SIGHUP，觸發 graceful worker reload。
    讓 HTTP response 先送出後再重載，不需要 sudo。
    """
    def _do():
        import time
        time.sleep(delay)
        try:
            master_pid = os.getppid()   # gunicorn worker → ppid = master
            os.kill(master_pid, signal.SIGHUP)
            logger.info(f"已對 gunicorn master (pid={master_pid}) 送出 SIGHUP，服務重載中")
        except Exception as e:
            logger.warning(f"自動重載失敗：{e}")

    threading.Thread(target=_do, daemon=True).start()


# ============================
# login_required 裝飾器
# ============================

def login_required(f):
    """WebGUI 頁面/API 需登入"""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            if request.is_json:
                return jsonify({"error": "unauthorized"}), 401
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated


# ============================
# 頁面路由（全部需登入）
# ============================

@gui.route("/")
@login_required
def dashboard():
    recent_calls = models.call_log_query(limit=10)
    groups = models.group_list()
    contacts = models.contact_list()
    rules = models.rule_list()
    return render_template("dashboard.html",
                           calls=recent_calls,
                           groups=groups,
                           contacts=contacts,
                           rules=rules)


@gui.route("/contacts")
@login_required
def contacts_page():
    contacts = models.contact_list()
    groups = models.group_list()
    return render_template("contacts.html",
                           contacts=contacts,
                           groups=groups)


@gui.route("/history")
@login_required
def history_page():
    page = request.args.get("page", 1, type=int)
    per_page = 20
    filters = {
        "alert_name": request.args.get("alert_name", ""),
        "resource_name": request.args.get("resource_name", ""),
        "result": request.args.get("result", ""),
        "date_from": request.args.get("date_from", ""),
        "date_to": request.args.get("date_to", ""),
    }
    data = models.call_log_query(
        limit=per_page,
        offset=(page - 1) * per_page,
        **filters
    )
    return render_template("call_history.html",
                           data=data,
                           page=page,
                           per_page=per_page,
                           filters=filters)


@gui.route("/settings")
@login_required
def settings_page():
    cfg = _masked(_read_yaml())
    return render_template("settings.html", cfg=cfg)


@gui.route("/routing")
@login_required
def routing_page():
    rules = models.rule_list()
    groups = models.group_list()
    return render_template("routing.html",
                           rules=rules,
                           groups=groups)


# ============================
# REST API — 聯絡人
# ============================

@gui.route("/api/contacts", methods=["GET"])
@login_required
def api_contacts_list():
    group_id = request.args.get("group_id", type=int)
    return jsonify(models.contact_list(group_id))


@gui.route("/api/contacts", methods=["POST"])
@login_required
def api_contacts_create():
    data = request.get_json()
    cid = models.contact_create(
        name=data["name"],
        number=data["number"],
        group_id=data.get("group_id", 1),
        priority=data.get("priority", 1),
        note=data.get("note", "")
    )
    return jsonify({"id": cid, "status": "created"}), 201


@gui.route("/api/contacts/<int:cid>", methods=["PUT"])
@login_required
def api_contacts_update(cid):
    data = request.get_json()
    models.contact_update(cid, **data)
    return jsonify({"status": "updated"})


@gui.route("/api/contacts/<int:cid>", methods=["DELETE"])
@login_required
def api_contacts_delete(cid):
    models.contact_delete(cid)
    return jsonify({"status": "deleted"})


# ============================
# REST API — 群組
# ============================

@gui.route("/api/groups", methods=["GET"])
@login_required
def api_groups_list():
    return jsonify(models.group_list())


@gui.route("/api/groups", methods=["POST"])
@login_required
def api_groups_create():
    data = request.get_json()
    gid = models.group_create(
        name=data["name"],
        description=data.get("description", "")
    )
    return jsonify({"id": gid, "status": "created"}), 201


@gui.route("/api/groups/<int:gid>", methods=["DELETE"])
@login_required
def api_groups_delete(gid):
    models.group_delete(gid)
    return jsonify({"status": "deleted"})


# ============================
# REST API — 路由規則
# ============================

@gui.route("/api/rules", methods=["GET"])
@login_required
def api_rules_list():
    return jsonify(models.rule_list())


@gui.route("/api/rules", methods=["POST"])
@login_required
def api_rules_create():
    data = request.get_json()
    rid = models.rule_create(
        name=data["name"],
        match_field=data.get("match_field", "resourceName"),
        match_pattern=data["match_pattern"],
        target_group_id=data["target_group_id"],
        priority=data.get("priority", 1),
        description=data.get("description", "")
    )
    return jsonify({"id": rid, "status": "created"}), 201


@gui.route("/api/rules/<int:rid>", methods=["DELETE"])
@login_required
def api_rules_delete(rid):
    models.rule_delete(rid)
    return jsonify({"status": "deleted"})


# ============================
# REST API — 通話紀錄
# ============================

@gui.route("/api/call-history", methods=["GET"])
@login_required
def api_call_history():
    return jsonify(models.call_log_query(
        limit=request.args.get("limit", 50, type=int),
        offset=request.args.get("offset", 0, type=int),
        alert_name=request.args.get("alert_name", ""),
        resource_name=request.args.get("resource_name", ""),
        result=request.args.get("result", ""),
        date_from=request.args.get("date_from", ""),
        date_to=request.args.get("date_to", ""),
    ))


# ============================
# REST API — 系統設定
# ============================

@gui.route("/api/settings", methods=["GET"])
@login_required
def api_settings_get():
    """讀取目前設定（敏感欄位以遮罩回傳）"""
    cfg = _read_yaml()
    return jsonify(_masked(cfg))


@gui.route("/api/settings", methods=["PUT"])
@login_required
def api_settings_put():
    """儲存設定並自動重載服務（SIGHUP to gunicorn master）"""
    updates = request.get_json(silent=True) or {}
    original = _read_yaml()
    merged = _merge_settings(original, updates)
    try:
        _write_yaml(merged)
        # 2 秒後對 gunicorn master 送 SIGHUP，讓 response 先送出
        _deferred_reload(delay=2.0)
        return jsonify({
            "status": "saved",
            "message": "設定已儲存，服務將在 2 秒後自動重載。"
        })
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================
# REST API — 連線測試
# ============================

@gui.route("/api/test-sip", methods=["POST"])
@login_required
def api_test_sip():
    """檢查 SIP 目前連線狀態"""
    try:
        from sip_caller import SipEngine
        ready = (
            SipEngine._instance is not None
            and SipEngine._instance.is_ready()
        )
        if ready:
            msg = "SIP 已成功連線並完成帳號註冊"
        else:
            msg = ("SIP 未連線。請確認 settings.yaml 中的 SIP 設定後重啟服務。\n"
                   "如使用 EZUC+，需申請 SIP Trunk 帳號（一般 App 帳號會收到 403 Forbidden）。")
        return jsonify({"registered": ready, "message": msg})
    except Exception as e:
        return jsonify({"registered": False, "message": f"狀態查詢失敗：{e}"}), 500


@gui.route("/api/test-twilio", methods=["POST"])
@login_required
def api_test_twilio():
    """驗證 Twilio 帳號憑證（不實際撥號）"""
    data = request.get_json(silent=True) or {}
    account_sid = data.get("account_sid", "")
    auth_token = data.get("auth_token", "")

    cfg = _read_yaml()
    twilio_cfg = cfg.get("twilio", {})
    if auth_token == _MASK:
        auth_token = twilio_cfg.get("auth_token", "")
    if not account_sid:
        account_sid = twilio_cfg.get("account_sid", "")

    if not account_sid or not auth_token:
        return jsonify({"ok": False, "message": "Account SID 與 Auth Token 不得為空"}), 400

    try:
        from twilio.rest import Client as TwilioClient
        client = TwilioClient(account_sid, auth_token)
        account = client.api.accounts(account_sid).fetch()
        return jsonify({
            "ok": True,
            "message": f"Twilio 驗證成功，帳號名稱：{account.friendly_name}"
        })
    except ImportError:
        return jsonify({"ok": False, "message": "twilio 套件未安裝，請執行：pip install twilio"}), 500
    except Exception as e:
        return jsonify({"ok": False, "message": f"驗證失敗：{e}"}), 400


@gui.route("/api/detect-url", methods=["GET"])
@login_required
def api_detect_url():
    """回傳伺服器目前可見的 base URL（供 Twilio public_base_url 參考）"""
    # 優先讀 X-Forwarded-Proto / X-Forwarded-Host（nginx proxy 場景）
    proto = request.headers.get("X-Forwarded-Proto", request.scheme)
    host = request.headers.get("X-Forwarded-Host", request.host)
    url = f"{proto}://{host}"
    is_https = proto == "https"
    return jsonify({
        "url": url,
        "is_https": is_https,
        "warning": None if is_https else "目前為 HTTP，Twilio 要求公開 URL 必須是 HTTPS。測試可用 ngrok。"
    })


# ============================
# REST API — 測試撥號
# ============================

@gui.route("/api/test-call", methods=["POST"])
@login_required
def api_test_call():
    """
    發送測試語音電話。
    Body: { "number": "+886912345678", "message": "自訂測試語音（選填）" }
    """
    data = request.get_json(silent=True) or {}
    number = data.get("number", "").strip()
    if not number:
        return jsonify({"ok": False, "message": "請輸入要撥打的電話號碼"}), 400

    cfg = _read_yaml()
    tts_cfg = cfg.get("tts", {})
    test_text = data.get("message", "") or "這是 vROps Alert 系統的測試通話，語音撥號功能正常運作，請放心。"

    # 合成測試語音
    try:
        from tts_engine import synthesize_speech
        wav_path = synthesize_speech(test_text, tts_cfg)
    except Exception as e:
        return jsonify({"ok": False, "message": f"語音合成失敗：{e}"}), 500

    # 依設定選擇後端撥號
    twilio_cfg = cfg.get("twilio", {})
    if twilio_cfg.get("enabled", False):
        try:
            from twilio_caller import make_twilio_call
            report = make_twilio_call(
                wav_path=wav_path,
                target_number=number,
                config=twilio_cfg
            )
        except ImportError:
            return jsonify({"ok": False, "message": "twilio 套件未安裝"}), 500
        except Exception as e:
            return jsonify({"ok": False, "message": f"Twilio 撥號失敗：{e}"}), 500
        backend = "Twilio"
    else:
        try:
            from sip_caller import make_sip_call
            sip_cfg = cfg.get("sip", {})
            report = make_sip_call(
                wav_path=wav_path,
                target_number=number,
                config=sip_cfg
            )
        except Exception as e:
            return jsonify({"ok": False, "message": f"SIP 撥號失敗：{e}"}), 500
        backend = "SIP"

    # CallResult 是 Enum，取 .value 轉成字串才能 JSON 序列化
    raw_result = getattr(report, "result", "")
    result_str = raw_result.value if hasattr(raw_result, "value") else str(raw_result)

    ok = result_str in ("answered", "completed", "no-answer", "success")
    err = getattr(report, "error_message", "") or ""
    duration = getattr(report, "duration_seconds", 0) or 0

    msg_parts = [f"[{backend}] 通話結果：{result_str}，號碼：{report.target}"]
    if duration:
        msg_parts.append(f"通話時長：{duration}s")
    if err and not ok:
        msg_parts.append(f"錯誤：{err}")

    return jsonify({
        "ok": ok,
        "message": "\n".join(msg_parts),
        "result": result_str,
        "backend": backend
    })
