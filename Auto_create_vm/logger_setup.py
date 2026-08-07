"""
Изолированная настройка логгера
"""

import logging


class ColoredFormatter(logging.Formatter):
    COLORS = {
        'INFO': '\033[36m',  # Голубой/циан
        'WARNING': '\033[33m',  # Желтый
        'ERROR': '\033[31m',  # Красный
        'CRITICAL': '\033[35m',  # Пурпурный
        'DEBUG': '\033[32m',  # Зеленый
    }
    RESET = '\033[0m'

    def format(self, record):
        # Cначала получаем отформотированное сообщение от родительского класса
        log_message = super().format(record)

        color = self.COLORS.get(record.levelname,'')
        if color:
            log_message = f"{color}{log_message}{self.RESET}"
        return log_message

def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    # Создаем handler-ы и избегаем их дублирование
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(ColoredFormatter(
            fmt="%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%H:%M:%S"
        ))
        logger.addHandler(handler)

    return logger
