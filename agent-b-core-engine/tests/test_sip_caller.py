#!/usr/bin/env python3
"""
test_sip_caller.py — SIP 撥號模組單元測試

注意：此模組依賴 pjsua2（需在 Ubuntu 環境編譯安裝），
在無 pjsua2 的環境執行時，大部分測試會跳過或使用 Mock。

測試項目：
1. CallResult enum 值正確
2. CallReport dataclass 建立
3. SipEngine 單例模式
4. make_sip_call() 對外介面
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock, PropertyMock

# 模擬 pjsua2（在沒有實際安裝的環境中）
pj_mock = MagicMock()
sys.modules["pjsua2"] = pj_mock
pj_mock.PJSIP_INV_STATE_CONFIRMED = 5
pj_mock.PJSIP_INV_STATE_DISCONNECTED = 6
pj_mock.PJMEDIA_TYPE_AUDIO = 1
pj_mock.PJSUA_CALL_MEDIA_ACTIVE = 1
pj_mock.PJMEDIA_FILE_NO_LOOP = 1
pj_mock.PJSIP_TLSV1_2_METHOD = 4
pj_mock.PJSIP_TRANSPORT_TLS = 3
pj_mock.PJSIP_TRANSPORT_UDP = 1

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sip_caller
from sip_caller import CallResult, CallReport, SipEngine


class TestCallResultEnum(unittest.TestCase):
    """CallResult 枚舉測試"""

    def test_success_value(self):
        self.assertEqual(CallResult.SUCCESS.value, "success")

    def test_no_answer_value(self):
        self.assertEqual(CallResult.NO_ANSWER.value, "no_answer")

    def test_busy_value(self):
        self.assertEqual(CallResult.BUSY.value, "busy")

    def test_failed_value(self):
        self.assertEqual(CallResult.FAILED.value, "failed")

    def test_timeout_value(self):
        self.assertEqual(CallResult.TIMEOUT.value, "timeout")


class TestCallReport(unittest.TestCase):
    """CallReport dataclass 測試"""

    def test_create_success_report(self):
        report = CallReport(
            result=CallResult.SUCCESS,
            target="1001",
            duration_seconds=5.5
        )
        self.assertEqual(report.result, CallResult.SUCCESS)
        self.assertEqual(report.target, "1001")
        self.assertAlmostEqual(report.duration_seconds, 5.5)
        self.assertEqual(report.error_message, "")

    def test_create_failed_report_with_error(self):
        report = CallReport(
            result=CallResult.FAILED,
            target="1002",
            duration_seconds=0.0,
            error_message="SIP 443 Forbidden"
        )
        self.assertEqual(report.error_message, "SIP 443 Forbidden")


class TestSipEngineSingleton(unittest.TestCase):
    """SipEngine 單例模式測試"""

    def setUp(self):
        # 重置單例
        SipEngine._instance = None

    def tearDown(self):
        # 清理單例
        SipEngine._instance = None

    def test_singleton_returns_same_instance(self):
        """同一 config 應回傳相同實例"""
        config = {
            "server": "test.server",
            "port": 5061,
            "transport": "tls",
            "username": "user",
            "password": "pass",
        }

        with patch.object(SipEngine, "_initialize"):
            inst1 = SipEngine.get_instance(config)
            inst2 = SipEngine.get_instance(config)

        self.assertIs(inst1, inst2)

    def test_uninitialized_make_call_returns_failed(self):
        """未初始化的引擎應回傳 FAILED"""
        config = {
            "server": "test.server",
            "port": 5061,
            "transport": "tls",
            "username": "user",
            "password": "pass",
        }

        with patch.object(SipEngine, "_initialize"):
            engine = SipEngine(config)
            engine._initialized = False

        report = engine.make_call("/tmp/test.wav", "1001")
        self.assertEqual(report.result, CallResult.FAILED)
        self.assertIn("未初始化", report.error_message)

    def test_is_ready_false_when_not_initialized(self):
        """未初始化時 is_ready() 應回傳 False"""
        config = {"server": "t", "port": 5061, "transport": "tls",
                  "username": "u", "password": "p"}

        with patch.object(SipEngine, "_initialize"):
            engine = SipEngine(config)
            engine._initialized = False

        self.assertFalse(engine.is_ready())


class TestMakeSipCallInterface(unittest.TestCase):
    """make_sip_call() 對外介面測試"""

    def setUp(self):
        SipEngine._instance = None

    def tearDown(self):
        SipEngine._instance = None

    def test_make_sip_call_uses_singleton(self):
        """make_sip_call() 應使用 SipEngine 單例"""
        config = {
            "server": "clouduc.e-usi.com",
            "port": 5061,
            "transport": "tls",
            "username": "test",
            "password": "test",
        }

        mock_engine = MagicMock()
        expected_report = CallReport(
            result=CallResult.SUCCESS,
            target="1001",
            duration_seconds=5.0
        )
        mock_engine.make_call.return_value = expected_report

        with patch.object(SipEngine, "get_instance", return_value=mock_engine):
            result = sip_caller.make_sip_call(
                wav_path="/tmp/test.wav",
                target_number="1001",
                config=config
            )

        mock_engine.make_call.assert_called_once_with("/tmp/test.wav", "1001")
        self.assertEqual(result.result, CallResult.SUCCESS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
