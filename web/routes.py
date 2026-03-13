#!/usr/bin/env python3
"""
routes.py — WebGUI 路由 + REST API
[v3 修正] 所有頁面和 API 都需要登入驗證
"""

import os
import functools
from flask import (
    Blueprint, render_template, request,
    jsonify, redirect, session
)
from web import models

_here = os.path.dirname(os.path.abspath(__file__))

gui = Blueprint("gui", __name__,
                template_folder=os.path.join(_here, "templates"),
                static_folder=os.path.join(_here, "static"),
                static_url_path="/static")


# [v3] login_required 裝飾器（在 Blueprint 中獨立定義，
# 避免與 webhook_server.py 的 circular import）
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
