#!/usr/bin/env python3
"""
twilio_caller.py — Twilio REST API 外撥模組 v1

作為 SIP (pjsua2) 的替代後端，透過 Twilio 發起電話並播放語音告警。
與 make_sip_call() 相同的函式介面，可在 alert_manager 中無縫切換。

流程：
  1. 呼叫 Twilio calls.create()，帶入 TwiML webhook URL
  2. Twilio 接通後回呼 /twiml/<audio_id>，取得 <Play> 指令
  3. Twilio 從 /audio/<filename> 下載 WAV 並播放
  4. 定期輪詢 call.status 直到完成（最多 120 秒）
"""

import os
import time
import logging
from typing import Optional

from sip_caller import CallResult, CallReport  # 共用結果資料結構

logger = logging.getLogger(__name__)

try:
    from twilio.rest import Client as TwilioClient
    TWILIO_AVAILABLE = True
except ImportError:
    TwilioClient = None
    TWILIO_AVAILABLE = False
    logger.warning(
        "twilio 套件未安裝 — Twilio 外撥停用。"
        "請執行：pip install twilio"
    )

# Twilio call.status 最終狀態集合
_TERMINAL_STATUSES = {"completed", "busy", "no-answer", "canceled", "failed"}

# 最長等待通話結束的秒數
_CALL_TIMEOUT = 120


def make_twilio_call(
    wav_path: str,
    target_number: str,
    config: dict
) -> CallReport:
    """
    透過 Twilio 發起電話並播放語音告警。

    Args:
        wav_path:      TTS 產生的 WAV 檔案路徑（需能透過 /audio/<filename> 存取）
        target_number: 撥打的電話號碼（E.164 格式，如 +886912345678）
        config:        settings.yaml 中的 twilio 區段，需包含：
                         account_sid, auth_token, from_number, public_base_url

    Returns:
        CallReport（與 make_sip_call() 相同格式）
    """
    if not TWILIO_AVAILABLE:
        logger.error("twilio 套件不可用，無法撥號至 %s", target_number)
        return CallReport(
            result=CallResult.FAILED,
            target=target_number,
            duration_seconds=0.0,
            error_message="twilio 套件未安裝"
        )

    account_sid = config.get("account_sid", "")
    auth_token = config.get("auth_token", "")
    from_number = config.get("from_number", "")
    public_base_url = config.get("public_base_url", "").rstrip("/")

    if not all([account_sid, auth_token, from_number, public_base_url]):
        logger.error("Twilio 設定不完整（account_sid/auth_token/from_number/public_base_url）")
        return CallReport(
            result=CallResult.FAILED,
            target=target_number,
            duration_seconds=0.0,
            error_message="Twilio 設定不完整"
        )

    # 從 wav_path 取出檔名，作為 TwiML webhook 的 audio_id
    audio_filename = os.path.basename(wav_path)
    twiml_url = f"{public_base_url}/twiml/{audio_filename}"

    start_time = time.time()
    call_sid: Optional[str] = None

    try:
        client = TwilioClient(account_sid, auth_token)

        logger.info(
            "Twilio 撥號中：%s → %s（TwiML: %s）",
            from_number, target_number, twiml_url
        )

        call = client.calls.create(
            url=twiml_url,
            to=target_number,
            from_=from_number,
            timeout=30,             # 響鈴超時（秒）
            time_limit=180          # 最長通話秒數（保護措施）
        )
        call_sid = call.sid
        logger.info("Twilio call SID：%s，初始狀態：%s", call_sid, call.status)

        # 輪詢通話狀態直到完成
        final_status = _poll_call_status(client, call_sid)

    except Exception as e:
        duration = time.time() - start_time
        logger.error("Twilio 撥號失敗：%s", e, exc_info=True)
        return CallReport(
            result=CallResult.FAILED,
            target=target_number,
            duration_seconds=duration,
            error_message=str(e)
        )

    duration = time.time() - start_time
    result = _status_to_call_result(final_status)

    logger.info(
        "Twilio 通話結果：%s（status=%s, target=%s, duration=%.1fs）",
        result.value, final_status, target_number, duration
    )

    return CallReport(
        result=result,
        target=target_number,
        duration_seconds=duration,
        error_message="" if result == CallResult.SUCCESS else final_status
    )


def _poll_call_status(client: "TwilioClient", call_sid: str) -> str:
    """
    輪詢 Twilio call 狀態直到進入終止狀態或逾時。

    Returns:
        最終的 status 字串（'completed', 'busy', 'no-answer', 'failed' 等）
    """
    deadline = time.time() + _CALL_TIMEOUT
    poll_interval = 3  # 每 3 秒查詢一次

    while time.time() < deadline:
        time.sleep(poll_interval)
        try:
            call_resource = client.calls(call_sid).fetch()
            status = call_resource.status
            logger.debug("Twilio call %s 狀態：%s", call_sid, status)

            if status in _TERMINAL_STATUSES:
                return status

        except Exception as e:
            logger.warning("輪詢 Twilio call 狀態失敗：%s", e)

    # 逾時——嘗試主動掛斷
    logger.warning(
        "Twilio 通話輪詢逾時（%ds），嘗試掛斷 %s", _CALL_TIMEOUT, call_sid
    )
    try:
        client.calls(call_sid).update(status="completed")
    except Exception:
        pass

    return "no-answer"


def _status_to_call_result(status: str) -> CallResult:
    """將 Twilio call status 對應到 CallResult enum。"""
    mapping = {
        "completed": CallResult.SUCCESS,
        "busy":      CallResult.BUSY,
        "no-answer": CallResult.NO_ANSWER,
        "canceled":  CallResult.NO_ANSWER,
        "failed":    CallResult.FAILED,
    }
    return mapping.get(status, CallResult.FAILED)
