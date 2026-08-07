"""
Базовый класс для всех тестов.
Каждый тест:
 - имеет уникальный ID (для Test IT)
 - возвращает TestResult (pass/fail/skip + details)
 - автоматически логируется
"""
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List
from Auto_create_vm.logger_setup import get_logger

logger = get_logger("BaseTest")

class Status(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"

@dataclass
class TestResult:
    test_id: str
    name: str
    status: Status
    duration_sec: float = 0.0
    message: str = ""
    attachments: List[str] = field(default_factory=list)
    step_results: List[dict] = field(default_factory=list)
    started_at: float = 0.0
    finished_at: float = 0.0

class BaseTest:
    """
    Базовый класс теста.
    Пример использования:
        class TestInstallation(BaseTest):
            test_id = "AUTO-123"
            name = "Проверка установки ПО"
            def run(self, vm_ip: str) -> TestResult:
                self.step("Подключение по SSH")
    """
    test_id: str = ""
    name: str = ""

    def __init__(self):
        self._result = TestResult(
            test_id=self.test_id,
            name=self.name,
            status=Status.PASSED,
        )
        self._logger = get_logger(self.__class__.__name__)

    def run(self, **context) -> TestResult:
        """запуск теста с замером времени"""
        self._result.started_at = time.time()
        self._logger.info(f"старт теста [{self.test_id}] {self.name}")
        try:
            self.execute(**context)
        except AssertionError as e:
            self._result.status = Status.FAILED
            self._result.message = str(e)
            self._logger.error(f"Fail: {e}")
        except Exception as e:
            self._result.status = Status.BLOCKED
            self._result.message = f"{type(e).__name__}: {e}"
            self._logger.error(f"Blocked: {e}")
        finally:
            self._result.finished_at = time.time()
            self._result.duration_sec = round(
                self._result.finished_at - self._result.started_at, 2
            )
            self._logger.info(
                f"{'done' if self._result.status == Status.PASSED else 'X'}"
                f"[{self.test_id}] {self._result.status.value}"
                f"({self._result.duration_sec}s)"
            )
        return self._result

    def execute(self, **context):
        """Преопределяется в наследниках"""
        raise NotImplementedError

    def step(self, description: str):
        """Фикс шага теста"""
        self._logger.info(f"+ {description}")
        self._result.step_results.append({
            "description": description,
            "time": time.time(),
        })

    def fail(self, message: str):
        """фейл с сообщением0"""
        raise AssertionError(message)

    def skip(self, reason: str):
        """Пропустить тест"""
        self._result.status = Status.SKIPPED
        self._result.message = reason
        raise _SkipException(reason)

    def attach(self, path: str):
        """Прикрепить лог к результату"""
        self._result.attachments.append(path)

class _SkipException(Exception):
    pass