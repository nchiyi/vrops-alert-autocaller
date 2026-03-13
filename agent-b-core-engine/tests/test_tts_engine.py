#!/usr/bin/env python3
"""
test_tts_engine.py — TTS 語音合成模組單元測試

測試項目：
1. synthesize_speech() 成功合成中文語音
2. TTS 失敗時降級到 fallback_alert.wav
3. fallback 不存在時降級到 pyttsx3
4. 語音清理功能
"""

import os
import sys
import time
import unittest
import tempfile
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tts_engine


class TestSynthesizeSpeech(unittest.TestCase):
    """synthesize_speech() 主函式測試"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config = {
            "engine": "edge-tts",
            "voice": "zh-TW-HsiaoChenNeural",
            "output_dir": self.tmpdir,
            "fallback_wav": os.path.join(self.tmpdir, "fallback.wav"),
        }

    def test_edge_tts_success(self):
        """edge-tts 成功合成時應回傳 WAV 路徑"""
        fake_wav = os.path.join(self.tmpdir, "alert_test.wav")
        # 建立假 WAV（44 bytes header + 32000 bytes = 1 秒）
        with open(fake_wav, "wb") as f:
            f.write(b"\x00" * (44 + 32000))

        def mock_edge_tts(text, output_path, config):
            # 模擬成功產生 mp3
            with open(output_path, "wb") as f:
                f.write(b"\xff\xfb" * 100)

        def mock_convert(input_path, output_path):
            # 模擬 ffmpeg 轉換成功
            with open(output_path, "wb") as f:
                f.write(b"\x00" * (44 + 32000))

        with patch("tts_engine._synthesize_edge_tts", side_effect=mock_edge_tts), \
             patch("tts_engine._convert_to_wav", side_effect=mock_convert):
            result = tts_engine.synthesize_speech(
                "測試告警，主機 CPU 超過 95%", self.config
            )

        self.assertTrue(result.endswith(".wav"))
        self.assertTrue(os.path.exists(result))

    def test_fallback_to_wav_on_tts_failure(self):
        """TTS 失敗時應降級使用 fallback_alert.wav"""
        # 建立 fallback wav
        with open(self.config["fallback_wav"], "wb") as f:
            f.write(b"\x00" * 1000)

        with patch("tts_engine._synthesize_edge_tts", side_effect=Exception("網路斷線")):
            result = tts_engine.synthesize_speech("測試", self.config)

        self.assertEqual(result, self.config["fallback_wav"])

    def test_fallback_to_pyttsx3_when_no_fallback_wav(self):
        """fallback.wav 不存在時應嘗試 pyttsx3"""
        fake_wav = os.path.join(self.tmpdir, "pyttsx3_output.wav")

        def mock_pyttsx3(text, output_path):
            with open(output_path, "wb") as f:
                f.write(b"\x00" * 1000)
            return output_path

        with patch("tts_engine._synthesize_edge_tts", side_effect=Exception("失敗")), \
             patch("tts_engine._synthesize_pyttsx3_fallback", side_effect=mock_pyttsx3):
            result = tts_engine.synthesize_speech("測試", self.config)

        self.assertEqual(result, fake_wav)

    def test_all_engines_fail_raises(self):
        """所有 TTS 引擎失敗時應拋出 RuntimeError"""
        with patch("tts_engine._synthesize_edge_tts", side_effect=Exception("edge fail")), \
             patch("tts_engine._synthesize_pyttsx3_fallback", side_effect=Exception("pyttsx3 fail")):
            with self.assertRaises(RuntimeError) as ctx:
                tts_engine.synthesize_speech("測試", self.config)

        self.assertIn("TTS 完全失敗", str(ctx.exception))

    def test_unsupported_engine_raises(self):
        """不支援的 TTS 引擎應拋出 ValueError"""
        bad_config = dict(self.config, engine="unknown_engine")
        with self.assertRaises((RuntimeError, ValueError)):
            tts_engine.synthesize_speech("測試", bad_config)


class TestCleanupOldAudio(unittest.TestCase):
    """cleanup_old_audio() 清理測試"""

    def test_removes_old_files(self):
        """超過時限的語音檔應被清理"""
        with tempfile.TemporaryDirectory() as tmpdir:
            old_file = os.path.join(tmpdir, "alert_old.wav")
            new_file = os.path.join(tmpdir, "alert_new.wav")

            with open(old_file, "wb") as f:
                f.write(b"\x00")
            with open(new_file, "wb") as f:
                f.write(b"\x00")

            # 設定舊檔案的修改時間為 2 小時前
            old_time = time.time() - 2 * 3600
            os.utime(old_file, (old_time, old_time))

            tts_engine.cleanup_old_audio(tmpdir, max_age_hours=1)

            self.assertFalse(os.path.exists(old_file), "舊語音檔應被清理")
            self.assertTrue(os.path.exists(new_file), "新語音檔應保留")


class TestEdgeTtsVoices(unittest.TestCase):
    """EDGE_TTS_ZH_VOICES 字典測試"""

    def test_default_voice_exists(self):
        """預設語音應在可用語音列表中"""
        self.assertIn(
            "zh-TW-HsiaoChenNeural",
            tts_engine.EDGE_TTS_ZH_VOICES
        )

    def test_voices_not_empty(self):
        """語音列表不應為空"""
        self.assertGreater(len(tts_engine.EDGE_TTS_ZH_VOICES), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
