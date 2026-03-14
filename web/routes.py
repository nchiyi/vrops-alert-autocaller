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
import subprocess
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


@gui.route("/api/rules/<int:rid>", methods=["PUT"])
@login_required
def api_rules_update(rid):
    """更新路由規則（支援部分欄位：enabled, priority, name, pattern 等）"""
    data = request.get_json(silent=True) or {}
    if "priority" in data:
        try:
            data["priority"] = int(data["priority"])
        except (ValueError, TypeError):
            pass
    if "enabled" in data:
        data["enabled"] = 1 if data["enabled"] else 0
    if "target_group_id" in data:
        try:
            data["target_group_id"] = int(data["target_group_id"])
        except (ValueError, TypeError):
            pass
    models.rule_update(rid, **data)
    return jsonify({"status": "updated"})


@gui.route("/api/rules/<int:rid>", methods=["DELETE"])
@login_required
def api_rules_delete(rid):
    models.rule_delete(rid)
    return jsonify({"status": "deleted"})


@gui.route("/api/rules/test", methods=["POST"])
@login_required
def api_rules_test():
    """
    路由測試器：模擬告警資料，回傳第一條匹配規則與通知順序。
    Body: { "resourceName": "...", "alertName": "...", "criticality": "..." }
    """
    import fnmatch as _fnmatch
    data = request.get_json(silent=True) or {}

    conn = models.get_db()
    rules = conn.execute(
        "SELECT r.*, g.name as group_name "
        "FROM routing_rules r "
        "JOIN contact_groups g ON r.target_group_id=g.id "
        "WHERE r.enabled=1 ORDER BY r.priority"
    ).fetchall()

    matched_rule = None
    for rule in rules:
        field = rule["match_field"]
        pattern = rule["match_pattern"]
        value = data.get(field, "")
        if _fnmatch.fnmatch(str(value), pattern):
            matched_rule = dict(rule)
            break

    group_id = matched_rule["target_group_id"] if matched_rule else 1
    matched_label = "matched" if matched_rule else "default"

    contacts = conn.execute(
        "SELECT name, number, priority FROM contacts "
        "WHERE group_id=? AND enabled=1 ORDER BY priority",
        (group_id,)
    ).fetchall()

    conn.close()
    return jsonify({
        "matched_rule": matched_rule,
        "matched_label": matched_label,
        "contacts": [dict(c) for c in contacts],
    })


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
                   "常見原因：帳號/密碼錯誤、SIP 伺服器不可達、防火牆封鎖 SIP 埠（5060/5061）。")
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

    # CallResult enum 值：success / no_answer / busy / failed / timeout
    ok = result_str == "success"
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


# ============================
# REST API — SSL / nginx 管理
# ============================

_SSL_HELPER = os.path.join(_BASE_DIR, "ssl_helper.sh")
_SSL_DIR = os.path.join(_BASE_DIR, "ssl")


def _run_ssl_helper(*args, timeout: int = 120):
    """以 sudo 執行 ssl_helper.sh，回傳 (returncode, stdout, stderr)"""
    cmd = ["sudo", _SSL_HELPER] + [str(a) for a in args]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


@gui.route("/api/ssl/status", methods=["GET"])
@login_required
def api_ssl_status():
    """回傳 nginx / SSL 憑證狀態"""
    cfg = _read_yaml()
    # 從 public_base_url 解析域名
    base_url = cfg.get("twilio", {}).get("public_base_url", "")
    domain = base_url.replace("https://", "").replace("http://", "").split("/")[0]

    # nginx 是否執行中
    nginx_running = False
    try:
        rc, out, _ = _run_ssl_helper("nginx-status", timeout=5)
        nginx_running = (out == "RUNNING")
    except Exception:
        pass

    # 憑證資訊
    cert = {"type": None, "expiry": None}
    if domain:
        try:
            rc, out, _ = _run_ssl_helper("cert-status", domain, timeout=10)
            if out.startswith("LE "):
                cert = {"type": "letsencrypt", "expiry": out[3:].strip()}
            elif out.startswith("CUSTOM "):
                cert = {"type": "custom", "expiry": out[7:].strip()}
        except Exception:
            pass
    elif os.path.isfile(os.path.join(_SSL_DIR, "fullchain.pem")):
        # 有上傳過憑證但 URL 未設定
        cert = {"type": "custom", "expiry": None}

    return jsonify({
        "nginx_running": nginx_running,
        "cert": cert,
        "domain": domain,
    })


@gui.route("/api/ssl/certbot", methods=["POST"])
@login_required
def api_ssl_certbot():
    """申請 Let's Encrypt 憑證並設定 nginx HTTPS"""
    data = request.get_json(silent=True) or {}
    domain = data.get("domain", "").strip()
    email = data.get("email", "").strip()

    if not domain:
        return jsonify({"ok": False, "message": "請輸入域名"}), 400
    if not email:
        return jsonify({"ok": False, "message": "請輸入 Email"}), 400

    cfg = _read_yaml()
    app_port = cfg.get("webhook", {}).get("port", 5000)

    try:
        # Step 1: 確認 nginx 已安裝
        rc, out, err = _run_ssl_helper("nginx-status", timeout=5)
        if out == "STOPPED":
            # 嘗試先安裝（如果尚未安裝）
            _run_ssl_helper("install-nginx", timeout=120)

        # Step 2: 申請憑證（certbot standalone，nginx 暫停 ~10 秒）
        rc, out, err = _run_ssl_helper("certbot", domain, email, timeout=180)
        if rc != 0 or "CERTBOT_OK" not in out:
            return jsonify({
                "ok": False,
                "message": f"Let's Encrypt 申請失敗：\n{err or out}\n\n"
                           f"請確認：1) DNS A Record 已指向本機 IP  "
                           f"2) Port 80 已開放且 NAT 轉發正確"
            }), 500

        # Step 3: 套用 nginx HTTPS 設定
        rc, out, err = _run_ssl_helper("apply-nginx", domain, str(app_port), timeout=30)
        if rc != 0 or "NGINX_OK" not in out:
            return jsonify({
                "ok": False,
                "message": f"nginx 設定失敗：\n{err or out}"
            }), 500

        # Step 4: 更新 public_base_url
        original = _read_yaml()
        original.setdefault("twilio", {})["public_base_url"] = f"https://{domain}"
        _write_yaml(original)

        return jsonify({
            "ok": True,
            "message": f"✓ Let's Encrypt 憑證申請成功！\n公開 HTTPS URL：https://{domain}\n"
                       f"Twilio public_base_url 已自動更新。"
        })

    except subprocess.TimeoutExpired:
        return jsonify({
            "ok": False,
            "message": "操作逾時（3 分鐘）。請確認 DNS 設定正確且 Port 80 可從外部連線。"
        }), 500
    except Exception as e:
        return jsonify({"ok": False, "message": f"執行錯誤：{e}"}), 500


@gui.route("/api/ssl/upload", methods=["POST"])
@login_required
def api_ssl_upload():
    """上傳自訂 SSL 憑證 (fullchain.pem + privkey.pem) 並套用 nginx"""
    cert_file = request.files.get("cert")
    key_file = request.files.get("key")
    domain = request.form.get("domain", "").strip()

    if not cert_file or not key_file:
        return jsonify({
            "ok": False,
            "message": "請同時上傳憑證檔（fullchain.pem）和私鑰檔（privkey.pem）"
        }), 400

    # vrops-alert 有寫入 ssl/ 目錄的權限（install.sh 建立時設定）
    try:
        os.makedirs(_SSL_DIR, exist_ok=True)
        cert_path = os.path.join(_SSL_DIR, "fullchain.pem")
        key_path = os.path.join(_SSL_DIR, "privkey.pem")
        cert_file.save(cert_path)
        key_file.save(key_path)
        os.chmod(cert_path, 0o644)
        os.chmod(key_path, 0o600)
    except Exception as e:
        return jsonify({"ok": False, "message": f"憑證儲存失敗：{e}"}), 500

    cfg = _read_yaml()
    app_port = cfg.get("webhook", {}).get("port", 5000)

    # 確認 nginx 已安裝
    try:
        rc, out, _ = _run_ssl_helper("nginx-status", timeout=5)
        if out == "STOPPED":
            _run_ssl_helper("install-nginx", timeout=120)
    except Exception:
        pass

    # 套用 nginx 設定
    try:
        effective_domain = domain or "_"
        rc, out, err = _run_ssl_helper("apply-custom", effective_domain, str(app_port), timeout=30)
        if rc != 0 or ("CUSTOM_OK" not in out and "NGINX_OK" not in out):
            return jsonify({
                "ok": False,
                "message": f"nginx 設定失敗：\n{err or out}"
            }), 500
    except Exception as e:
        return jsonify({"ok": False, "message": f"nginx 設定失敗：{e}"}), 500

    # 更新 public_base_url
    if domain:
        original = _read_yaml()
        original.setdefault("twilio", {})["public_base_url"] = f"https://{domain}"
        _write_yaml(original)

    return jsonify({
        "ok": True,
        "message": f"✓ 自訂 SSL 憑證已上傳並套用！\n"
                   + (f"公開 HTTPS URL：https://{domain}\nTwilio public_base_url 已自動更新。"
                      if domain else "nginx 已套用憑證，請手動更新 Twilio public_base_url。")
    })


@gui.route("/api/ssl/install-nginx", methods=["POST"])
@login_required
def api_ssl_install_nginx():
    """安裝 nginx + certbot（若尚未安裝）"""
    try:
        rc, out, err = _run_ssl_helper("install-nginx", timeout=180)
        if rc != 0 or "INSTALL_OK" not in out:
            return jsonify({"ok": False, "message": f"nginx 安裝失敗：\n{err or out}"}), 500
        return jsonify({"ok": True, "message": "✓ nginx + certbot 安裝完成"})
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "message": "安裝逾時"}), 500
    except Exception as e:
        return jsonify({"ok": False, "message": f"安裝失敗：{e}"}), 500


# ============================
# REST API — 帳號密碼管理
# ============================

@gui.route("/api/auth/password", methods=["PUT"])
@login_required
def api_change_password():
    """
    修改登入帳號或密碼（需先驗證目前密碼）。
    Body: {
        "current_password": "目前密碼（必填）",
        "new_username":     "新帳號（選填，留空則不變）",
        "new_password":     "新密碼（選填，留空則不變，至少 6 字元）"
    }
    成功後寫入 settings.yaml 並觸發服務 SIGHUP reload，
    前端應在收到 need_relogin=true 後導向 /logout 讓用戶重新登入。
    """
    data = request.get_json(silent=True) or {}
    current_password = data.get("current_password", "")
    new_username = data.get("new_username", "").strip()
    new_password = data.get("new_password", "")

    if not current_password:
        return jsonify({"ok": False, "message": "請輸入目前密碼"}), 400

    cfg = _read_yaml()
    users = cfg.get("webgui", {}).get("users", {})
    current_username = session.get("username", "")

    # 驗證目前密碼
    if not current_username or users.get(current_username) != current_password:
        logger.warning(f"帳號密碼修改失敗（目前密碼錯誤）：user={current_username} from {request.remote_addr}")
        return jsonify({"ok": False, "message": "目前密碼錯誤"}), 400

    # 決定目標帳號名稱
    target_username = new_username if new_username else current_username

    # 驗證新密碼長度
    if new_password and len(new_password) < 6:
        return jsonify({"ok": False, "message": "新密碼至少 6 個字元"}), 400

    # 更新 settings.yaml
    if "webgui" not in cfg:
        cfg["webgui"] = {}
    if "users" not in cfg["webgui"]:
        cfg["webgui"]["users"] = {}

    # 若帳號名稱改變，刪除舊 key
    if target_username != current_username and current_username in cfg["webgui"]["users"]:
        del cfg["webgui"]["users"][current_username]

    # 更新密碼（若未提供新密碼則保留舊密碼）
    cfg["webgui"]["users"][target_username] = new_password if new_password else current_password

    try:
        _write_yaml(cfg)
    except PermissionError as e:
        return jsonify({"ok": False, "message": str(e)}), 500

    logger.info(f"帳號密碼已更新：{current_username} → {target_username}")

    # 更新 session 中的 username（不立即登出，讓 reload 後重新登入）
    session["username"] = target_username

    # 觸發 gunicorn graceful reload，讓 webhook_server.py 重新載入 WEBGUI_USERS
    _deferred_reload(2.0)

    return jsonify({
        "ok": True,
        "message": "帳號密碼已更新，服務將在 2 秒後重載，請重新登入",
        "need_relogin": True
    })
