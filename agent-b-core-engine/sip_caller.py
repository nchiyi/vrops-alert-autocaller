#!/usr/bin/env python3
"""
sip_caller.py — SIP 撥號模組（EZUC+ 專用）v2
透過 pjsua2 發起 SIP TLS 通話並播放語音檔。

Agent B — 核心引擎
參考文件: 05-核心程式-SIP撥號-EZUC.md, 10-審查與修正報告.md

修正：
- SipEngine 單例模式（啟動一次，全程復用）
- Player 存為實例變數避免 GC
- 線程安全的撥號鎖
"""

import os
import time
import logging
import threading
from enum import Enum
from dataclasses import dataclass
from typing import Optional

import pjsua2 as pj

logger = logging.getLogger(__name__)


# ============================
# 資料結構
# ============================

class CallResult(Enum):
    """通話結果"""
    SUCCESS = "success"         # 對方接聽且播放完成
    NO_ANSWER = "no_answer"    # 無人接聽
    BUSY = "busy"              # 忙線中
    FAILED = "failed"          # 通話失敗
    TIMEOUT = "timeout"        # 超時


@dataclass
class CallReport:
    """通話報告"""
    result: CallResult
    target: str
    duration_seconds: float
    error_message: str = ""


# ============================
# pjsua2 回呼類別
# ============================

class AlertCall(pj.Call):
    """
    自訂 Call 類別，處理通話事件。
    接聽後播放 WAV，播放完畢掛斷。
    """

    def __init__(self, acc, wav_path: str):
        super().__init__(acc)
        self.wav_path = wav_path
        self.connected = False
        self.completed = False
        self.call_result = CallResult.FAILED
        self._event = threading.Event()
        # [修正] Player 存為實例變數，避免被 GC 回收導致播放中斷
        self._player: Optional[pj.AudioMediaPlayer] = None

    def onCallState(self, prm):
        """通話狀態變更回呼"""
        ci = self.getInfo()
        logger.info(f"通話狀態: {ci.stateText} (code={ci.lastStatusCode})")

        if ci.state == pj.PJSIP_INV_STATE_CONFIRMED:
            self.connected = True
            logger.info("對方已接聽")

        elif ci.state == pj.PJSIP_INV_STATE_DISCONNECTED:
            # 通話結束，清理 player
            self._player = None

            if self.connected and self.completed:
                self.call_result = CallResult.SUCCESS
            elif ci.lastStatusCode == 486:
                self.call_result = CallResult.BUSY
            elif ci.lastStatusCode in (408, 480):
                self.call_result = CallResult.NO_ANSWER
            elif self.connected and not self.completed:
                # 對方接聽但提前掛斷（算成功，至少部分聽到了）
                self.call_result = CallResult.SUCCESS
            else:
                self.call_result = CallResult.FAILED

            self._event.set()

    def onCallMediaState(self, prm):
        """媒體就緒回呼 — 在此播放語音檔"""
        ci = self.getInfo()

        for mi_idx in range(len(ci.media)):
            if ci.media[mi_idx].type == pj.PJMEDIA_TYPE_AUDIO:
                if ci.media[mi_idx].status == pj.PJSUA_CALL_MEDIA_ACTIVE:
                    try:
                        call_media = self.getAudioMedia(mi_idx)

                        # [修正] 存為 self._player，防止 GC 回收
                        self._player = pj.AudioMediaPlayer()
                        self._player.createPlayer(
                            self.wav_path,
                            pj.PJMEDIA_FILE_NO_LOOP
                        )
                        self._player.startTransmit(call_media)

                        logger.info(f"開始播放語音: {self.wav_path}")

                        wav_duration = self._get_wav_duration()

                        def delayed_hangup():
                            time.sleep(wav_duration + 2)
                            self.completed = True
                            try:
                                self.hangup(pj.CallOpParam())
                                logger.info("語音播放完成，已掛斷")
                            except pj.Error:
                                # 對方可能已先掛斷
                                pass

                        t = threading.Thread(
                            target=delayed_hangup, daemon=True
                        )
                        t.start()

                    except Exception as e:
                        logger.error(f"播放語音失敗: {e}")

    def _get_wav_duration(self) -> float:
        """估算 WAV 檔案播放長度（秒）"""
        try:
            size = os.path.getsize(self.wav_path)
            # [修正] 扣除 WAV header (44 bytes)
            # 16kHz, 16-bit, mono = 32000 bytes/sec
            return max((size - 44) / 32000.0, 1.0)
        except Exception:
            return 15.0

    def wait_for_completion(self, timeout: int = 60) -> CallResult:
        """等待通話完成，回傳結果"""
        self._event.wait(timeout=timeout)
        if not self._event.is_set():
            # 超時，嘗試掛斷
            try:
                self.hangup(pj.CallOpParam())
            except pj.Error:
                pass
            self.call_result = CallResult.TIMEOUT
        return self.call_result


# ============================
# SIP 帳號類別
# ============================

class AlertAccount(pj.Account):
    """SIP 帳號，處理註冊狀態"""

    def __init__(self):
        super().__init__()
        self.registered = False
        self._event = threading.Event()

    def onRegState(self, prm):
        """註冊狀態回呼"""
        info = self.getInfo()
        if info.regIsActive:
            self.registered = True
            logger.info("SIP 註冊成功")
        else:
            self.registered = False
            logger.warning(f"SIP 註冊失敗: {info.regStatus}")
        self._event.set()

    def wait_for_registration(self, timeout: int = 15) -> bool:
        """等待註冊完成"""
        self._event.wait(timeout=timeout)
        return self.registered


# ============================
# [修正] SIP 引擎單例
# ============================

class SipEngine:
    """
    SIP 引擎單例。

    整個服務生命週期只初始化一次 Endpoint、Transport、Account。
    所有撥號共用同一個 SIP 註冊。以 _call_lock 確保同一時間只有一通電話。
    """

    _instance: Optional['SipEngine'] = None
    _init_lock = threading.Lock()

    @classmethod
    def get_instance(cls, config: dict) -> 'SipEngine':
        """取得單例實例（線程安全）"""
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = cls(config)
        return cls._instance

    def __init__(self, config: dict):
        self.config = config
        self._call_lock = threading.Lock()  # 確保一次只有一通電話
        self._ep = None
        self._acc = None
        self._initialized = False
        self._initialize()

    def _initialize(self):
        """一次性初始化 pjsua2 Endpoint + Transport + Account"""
        sip_server = self.config["server"]
        sip_port = self.config["port"]
        sip_user = self.config["username"]
        sip_pass = self.config["password"]
        transport = self.config.get("transport", "tls")

        try:
            # 1. 建立 Endpoint（全域唯一）
            self._ep = pj.Endpoint()
            self._ep.libCreate()

            ep_cfg = pj.EpConfig()
            ep_cfg.logConfig.level = 3
            ep_cfg.logConfig.consoleLevel = 2
            # 設定 worker thread，處理 SIP 事件
            ep_cfg.uaConfig.threadCnt = 1
            ep_cfg.uaConfig.mainThreadOnly = False
            self._ep.libInit(ep_cfg)

            # 2. 建立 TLS Transport（一次）
            tp_cfg = pj.TransportConfig()
            if transport == "tls":
                tp_cfg.port = 0
                tp_cfg.tlsConfig.method = pj.PJSIP_TLSV1_2_METHOD
                tp_cfg.tlsConfig.verifyServer = False  # 正式環境改 True
                self._ep.transportCreate(pj.PJSIP_TRANSPORT_TLS, tp_cfg)
            else:
                tp_cfg.port = 5060
                self._ep.transportCreate(pj.PJSIP_TRANSPORT_UDP, tp_cfg)

            self._ep.libStart()
            logger.info("pjsua2 引擎啟動（單例）")

            # 3. 註冊帳號到 EZUC+（一次）
            acc_cfg = pj.AccountConfig()
            acc_cfg.idUri = f"sip:{sip_user}@{sip_server}"
            acc_cfg.regConfig.registrarUri = (
                f"sip:{sip_server}:{sip_port};transport={transport}"
            )
            acc_cfg.regConfig.timeoutSec = 300  # 每 5 分鐘自動續註冊

            cred = pj.AuthCredInfo(
                "digest", "*", sip_user, 0, sip_pass
            )
            acc_cfg.sipConfig.authCreds.append(cred)
            acc_cfg.sipConfig.proxies.append(
                f"sip:{sip_server}:{sip_port};transport={transport}"
            )

            self._acc = AlertAccount()
            self._acc.create(acc_cfg)

            if not self._acc.wait_for_registration(timeout=15):
                raise RuntimeError("SIP 註冊到 EZUC+ 失敗")

            self._initialized = True
            logger.info(
                f"SIP 引擎就緒: {sip_user}@{sip_server}:{sip_port} ({transport})"
            )

        except Exception as e:
            logger.error(f"SIP 引擎初始化失敗: {e}", exc_info=True)
            self.shutdown()
            raise

    def make_call(self, wav_path: str, target_number: str) -> CallReport:
        """
        發起 SIP 通話並播放語音。
        使用 _call_lock 確保同一時間只有一通電話。
        """
        if not self._initialized:
            return CallReport(
                result=CallResult.FAILED,
                target=target_number,
                duration_seconds=0,
                error_message="SIP 引擎未初始化"
            )

        start_time = time.time()
        report = CallReport(
            result=CallResult.FAILED,
            target=target_number,
            duration_seconds=0
        )

        # 同一時間只允許一通電話
        with self._call_lock:
            try:
                sip_uri = (
                    f"sip:{target_number}@{self.config['server']}"
                )
                logger.info(f"撥號中: {sip_uri}")

                call = AlertCall(self._acc, wav_path)
                call_prm = pj.CallOpParam()
                call_prm.opt.audioCount = 1
                call_prm.opt.videoCount = 0
                call.makeCall(sip_uri, call_prm)

                # 等待通話完成（含響鈴 30 秒 + 播放 + 緩衝）
                result = call.wait_for_completion(timeout=90)

                report.result = result
                report.duration_seconds = time.time() - start_time

                logger.info(
                    f"通話結果: {result.value} "
                    f"(target={target_number}, "
                    f"duration={report.duration_seconds:.1f}s)"
                )

            except Exception as e:
                report.error_message = str(e)
                report.duration_seconds = time.time() - start_time
                logger.error(f"SIP 撥號錯誤: {e}", exc_info=True)

        return report

    def is_ready(self) -> bool:
        """檢查引擎是否就緒"""
        return (
            self._initialized
            and self._acc is not None
            and self._acc.registered
        )

    def shutdown(self):
        """關閉 SIP 引擎（服務停止時呼叫）"""
        if self._ep:
            try:
                self._ep.libDestroy()
                logger.info("SIP 引擎已關閉")
            except Exception:
                pass
        self._initialized = False
        SipEngine._instance = None


# ============================
# 對外介面（保持向後相容）
# ============================

def make_sip_call(
    wav_path: str, target_number: str, config: dict
) -> CallReport:
    """
    透過 EZUC+ 發起 SIP TLS 通話並播放語音告警。

    內部使用 SipEngine 單例，自動復用已有的 SIP 連線。
    """
    engine = SipEngine.get_instance(config)
    return engine.make_call(wav_path, target_number)


# ============================
# 測試
# ============================

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)

    test_config = {
        "server": "clouduc.e-usi.com",
        "port": 5061,
        "transport": "tls",
        "username": "YOUR_SIP_USER",
        "password": "YOUR_SIP_PASS",
    }

    # 測試前需先用 tts_engine.py 產生 WAV
    report = make_sip_call(
        wav_path="audio/test_alert.wav",
        target_number="1001",
        config=test_config
    )

    print(f"結果: {report.result.value}")
    print(f"目標: {report.target}")
    print(f"耗時: {report.duration_seconds:.1f}s")
    if report.error_message:
        print(f"錯誤: {report.error_message}")

    # 測試完畢關閉引擎
    SipEngine.get_instance(test_config).shutdown()
