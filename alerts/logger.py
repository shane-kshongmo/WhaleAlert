"""
本地日志记录模块
"""
import os
import logging
import time
from config import LOG_DIR, LOG_LEVEL


def setup_logging():
    """初始化日志系统"""
    os.makedirs(LOG_DIR, exist_ok=True)

    log_file = os.path.join(LOG_DIR, f"whale_alert_{time.strftime('%Y%m%d')}.log")

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 文件 handler
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)

    # 控制台 handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))

    # 预警专用文件
    alert_file = os.path.join(LOG_DIR, "alerts.log")
    alert_handler = logging.FileHandler(alert_file, encoding="utf-8")
    alert_handler.setFormatter(formatter)
    alert_handler.setLevel(logging.WARNING)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(alert_handler)

    logging.info("日志系统初始化完成")
