#!/usr/bin/env python3
"""
tts_engine.py — 文字轉語音模組
將告警文字合成為 WAV 語音檔，供 SIP 撥號時播放。

Agent B — 核心引擎
參考文件: 04-核心程式-語音合成TTS.md, 10-審查與修正報告.md
"""

import os
import uuid
import asyncio
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def synthesize_speech(text: str, config: dict) -> str:
    """
    將文字合成語音，輸出 WAV 檔案路徑。

    [v3 優化] TTS 失敗時自動降級使用備援語音檔 (fallback_alert.wav)。
    確保即使網路斷線（edge-tts/gTTS 無法連線），電話仍然能撥出。

    Args:
        text: 要合成的文字
        config: TTS 設定（來自 settings.yaml 的 tts 區段）

    Returns:
        str: WAV 檔案的絕對路徑
    """
    engine = config.get("engine", "edge-tts")
    output_dir = config.get("output_dir", "/opt/vrops-alert-caller/audio")
    fallback_path = config.get(
        "fallback_wav",
        "/opt/vrops-alert-caller/audio/fallback_alert.wav"
    )

    os.makedirs(output_dir, exist_ok=True)

    file_id = uuid.uuid4().hex[:8]
    mp3_path = os.path.join(output_dir, f"alert_{file_id}.mp3")
    wav_path = os.path.join(output_dir, f"alert_{file_id}.wav")

    try:
        # 嘗試線上 TTS 合成
        if engine == "edge-tts":
            _synthesize_edge_tts(text, mp3_path, config)
        elif engine == "gtts":
            _synthesize_gtts(text, mp3_path, config)
        else:
            raise ValueError(f"不支援的 TTS 引擎: {engine}")

        _convert_to_wav(mp3_path, wav_path)

        if os.path.exists(mp3_path):
            os.remove(mp3_path)

        logger.info(f"TTS 合成完成: {wav_path} ({os.path.getsize(wav_path)} bytes)")
        return wav_path

    except Exception as e:
        # [v3] TTS 失敗 → 降級使用備援語音
        logger.error(f"TTS 合成失敗 ({engine}): {e}")

        if os.path.exists(fallback_path):
            logger.warning(f"降級使用備援語音: {fallback_path}")
            return fallback_path
        else:
            # 備援檔也不存在，嘗試用 pyttsx3 離線合成
            logger.warning("備援語音不存在，嘗試離線 pyttsx3 合成...")
            try:
                return _synthesize_pyttsx3_fallback(text, wav_path)
            except Exception as e2:
                logger.critical(f"所有 TTS 引擎均失敗: {e2}")
                raise RuntimeError(
                    f"TTS 完全失敗 (主要: {e}, 備援: {e2})"
                )


# ============================
# Edge TTS（推薦，品質最佳）
# ============================

def _synthesize_edge_tts(text: str, output_path: str, config: dict):
    """
    使用 Microsoft Edge TTS 合成語音。
    免費、品質極高、支援多種中文聲音。

    [v2 修正] 使用 new_event_loop() 取代 asyncio.run()，
    避免在子線程或已有 event loop 的環境中拋出 RuntimeError。
    """
    import edge_tts

    voice = config.get("voice", "zh-TW-HsiaoChenNeural")

    async def _generate():
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(output_path)

    # [修正] 不用 asyncio.run()，改用獨立的 event loop
    # asyncio.run() 在子線程中會拋出:
    #   RuntimeError: There is no current event loop in thread 'xxx'
    # 或在已有 loop 的環境中:
    #   RuntimeError: This event loop is already running
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_generate())
    finally:
        loop.close()

    logger.info(f"Edge TTS 合成: voice={voice}")


# ============================
# Google TTS（備選）
# ============================

def _synthesize_gtts(text: str, output_path: str, config: dict):
    """
    使用 Google TTS (gTTS) 合成語音。
    免費、簡單、品質中等。
    """
    from gtts import gTTS

    lang = config.get("language", "zh-TW")

    tts = gTTS(text=text, lang=lang)
    tts.save(output_path)
    logger.info(f"gTTS 合成: lang={lang}")


# ============================
# 格式轉換
# ============================

def _convert_to_wav(input_path: str, output_path: str):
    """
    將 MP3 轉換為 pjsua2 支援的 WAV 格式。

    格式要求：
    - Sample Rate: 16000 Hz
    - Channels: 1 (mono)
    - Sample Format: 16-bit signed PCM (s16le)
    """
    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-ar", "16000",        # 16kHz
        "-ac", "1",            # mono
        "-sample_fmt", "s16",  # 16-bit PCM
        "-acodec", "pcm_s16le",
        output_path
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=30
    )

    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg 轉換失敗: {result.stderr}")

    logger.debug(f"WAV 轉換完成: {output_path}")


# ============================
# [v3] pyttsx3 離線合成備援
# ============================

def _synthesize_pyttsx3_fallback(text: str, output_path: str) -> str:
    """
    最後手段：使用 pyttsx3 離線合成。
    不需網路，但語音品質較差。
    需要 apt install espeak 或 espeak-ng。
    """
    import pyttsx3

    engine = pyttsx3.init()
    engine.setProperty("rate", 150)
    engine.save_to_file(text, output_path)
    engine.runAndWait()

    logger.info(f"pyttsx3 離線合成完成: {output_path}")
    return output_path


def generate_fallback_wav(output_path: str = None):
    """
    預先合成一個備援語音檔案（安裝時執行一次）。
    確保即使所有 TTS 引擎都失敗，至少能播放這個檔案。
    """
    if output_path is None:
        output_path = "/opt/vrops-alert-caller/audio/fallback_alert.wav"

    text = (
        "注意，系統發生緊急告警。"
        "語音合成服務暫時無法使用，詳細內容無法播報。"
        "請立即登入 vROps 儀表板查看告警詳情。"
        "重複一次，請立即登入系統檢查。"
    )

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # 優先用 edge-tts 合成
    try:
        mp3_tmp = output_path.replace(".wav", ".mp3")
        _synthesize_edge_tts(text, mp3_tmp, {"voice": "zh-TW-HsiaoChenNeural"})
        _convert_to_wav(mp3_tmp, output_path)
        os.remove(mp3_tmp)
        print(f"✅ 備援語音已產生: {output_path}")
        return
    except Exception as e:
        print(f"edge-tts 失敗: {e}，嘗試 pyttsx3...")

    # 降級用 pyttsx3
    try:
        _synthesize_pyttsx3_fallback(text, output_path)
        print(f"✅ 備援語音已產生 (pyttsx3): {output_path}")
    except Exception as e:
        print(f"❌ 備援語音產生失敗: {e}")


# ============================
# 語音檔清理
# ============================

def cleanup_old_audio(output_dir: str, max_age_hours: int = 24):
    """
    清理超過指定時間的語音暫存檔。
    建議由 cron 或定時任務呼叫。
    """
    import time

    now = time.time()
    count = 0

    for f in Path(output_dir).glob("alert_*.wav"):
        if now - f.stat().st_mtime > max_age_hours * 3600:
            f.unlink()
            count += 1

    if count > 0:
        logger.info(f"清理了 {count} 個過期語音檔")


# ============================
# 可用語音列表（Edge TTS）
# ============================
EDGE_TTS_ZH_VOICES = {
    "zh-TW-HsiaoChenNeural": "台灣女聲（推薦）",
    "zh-TW-YunJheNeural": "台灣男聲",
    "zh-CN-XiaoxiaoNeural": "大陸女聲",
    "zh-CN-YunxiNeural": "大陸男聲",
    "zh-CN-YunyangNeural": "大陸男聲（新聞風格）",
}


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--generate-fallback":
        # 安裝時執行，預先產生備援語音
        generate_fallback_wav()
    else:
        # 一般測試
        test_config = {
            "engine": "edge-tts",
            "voice": "zh-TW-HsiaoChenNeural",
            "output_dir": "./audio"
        }

        path = synthesize_speech(
            "注意，這是測試告警。主機名稱：test-vm-01。告警項目：CPU 使用率超過 95%。",
            test_config
        )
        print(f"產生語音檔: {path}")
