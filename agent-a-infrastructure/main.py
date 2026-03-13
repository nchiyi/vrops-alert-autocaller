#!/usr/bin/env python3
"""
main.py — 主程式入口
可直接執行（開發用）或由 gunicorn 載入（正式部署）。

正式部署：
    gunicorn --bind 0.0.0.0:5000 --workers 1 --threads 4 --timeout 120 webhook_server:app

開發執行：
    python main.py
"""

import yaml
import logging
import sys
import os

# 確保當前目錄在 import 路徑中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def setup_logging(config: dict) -> None:
    log_cfg = config.get("logging", {})
    log_level = getattr(logging, log_cfg.get("level", "INFO"), logging.INFO)
    log_file = log_cfg.get("file", "/opt/vrops-alert-caller/logs/app.log")

    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )


def load_config(path: str = "config/settings.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def main() -> None:
    config_path = os.environ.get("CONFIG_PATH", "config/settings.yaml")
    config = load_config(config_path)

    setup_logging(config)
    logger = logging.getLogger(__name__)

    logger.info("=== vROps Alert AutoCaller 啟動 ===")
    logger.info(f"Webhook: http://0.0.0.0:{config['webhook']['port']}")
    logger.info(f"SIP: {config['sip']['server']}:{config['sip']['port']}")
    logger.info(f"TTS Engine: {config['tts']['engine']}")

    # 延遲 import，確保 logging 已設定
    from webhook_server import app, CONFIG  # noqa: F401

    app.run(
        host=config["webhook"]["host"],
        port=config["webhook"]["port"],
        debug=False,
        threaded=True,
    )


if __name__ == "__main__":
    main()
