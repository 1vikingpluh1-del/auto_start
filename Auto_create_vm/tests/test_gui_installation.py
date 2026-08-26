"""
Тест автоматической GUI-установки ПО на Windows VM.
Запускает gui_install_automation.py на ВМ, ждёт результат и анализирует его.
"""
import json
import os
import time

from Auto_create_vm.tests.base_tests import BaseTest
from Auto_create_vm.utils.winrm_client import WinRMClient
from Auto_create_vm.logger_setup import get_logger

logger = get_logger("TestGuiInstallation")


class TestGuiInstallation(BaseTest):
    test_id = "WIN-GUI-INSTALL-001"
    name = "Автоматическая GUI-установка приложения"

    def __init__(
            self,
            winrm_client: WinRMClient,
            installer_path: str,
            install_path: str,
            user: str,
            python_exe: str,
            runner_path: str,
            result_path: str,
            log_path: str,
            timeout_sec: int = 1800,
            mode: str = "full_install",
    ):
        super().__init__()
        self.winrm = winrm_client
        self.installer_path = installer_path
        self.install_path = install_path
        self.user = user
        self.python_exe = python_exe
        self.runner_path = runner_path
        self.result_path = result_path
        self.log_path = log_path
        self.timeout_sec = timeout_sec
        self.mode = mode

    def execute(self, **context):
        self.step(f"Проверка наличия Python и runner на ВМ")
        if not self.winrm.file_exists(self.python_exe):
            self.fail(f"Python не найден на ВМ по пути {self.python_exe}")
        if not self.winrm.file_exists(self.runner_path):
            self.fail(f"GUI runner не найден на ВМ по пути {self.runner_path}")

        self.step("Очистка старых результатов и логов GUI automation")
        self.winrm.delete_file(self.result_path)
        self.winrm.delete_file(self.log_path)

        self.step("Запуск GUI automation через интерактивный Scheduled Task")
        arguments = (
            f'"{self.runner_path}" '
            f'--installer "{self.installer_path}" '
            f'--install-path "{self.install_path}" '
            f'--result "{self.result_path}" '
            f'--log "{self.log_path}" '
            f'--timeout {self.timeout_sec} '
            f'--mode {self.mode}'
        )

        start_time = time.time()

        self.winrm.run_interactive(
            exe_path=self.python_exe,
            arguments=arguments,
            user=self.user,
            task_name="GuiAutomationTask",
        )

        self.step(f"Ожидание завершения GUI automation (файл {self.result_path})")
        if not self.winrm.wait_for_remote_file(self.result_path, timeout_sec=self.timeout_sec + 120):
            local_log = f"reports/gui_log_timeout_{int(time.time())}.log"
            try:
                self.winrm.download_file(self.log_path, local_log)
                self.attach(local_log)
            except Exception:
                pass
            self.fail("GUI automation не завершилась вовремя (result.json не создан)")

        elapsed_time = time.time() - start_time

        if elapsed_time < 30:
            logger.warning(
                f"⚠️ GUI automation завершилась подозрительно быстро ({elapsed_time:.1f} сек). "
                f"Возможно, произошла ошибка на раннем этапе."
            )

        self.step("Скачивание и анализ результата GUI automation")
        os.makedirs("reports", exist_ok=True)
        local_result = f"reports/gui_result_{int(time.time())}.json"
        self.winrm.download_file(self.result_path, local_result)
        self.attach(local_result)

        try:
            with open(local_result, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception as e:
            self.fail(f"Не удалось прочитать JSON результат GUI automation: {e}")

        if os.path.getsize(local_result) < 50:
            self.fail(f"Файл результата слишком мал ({os.path.getsize(local_result)} байт). Возможно, повреждён.")

        status = payload.get("status", "failed")
        error = payload.get("error", "")
        steps = payload.get("steps", [])

        # === НОВОЕ: Логируем known_bugs и observations из VM-скрипта ===
        for bug in payload.get("known_bugs", []):
            logger.warning(f"🐞 Известный баг: {bug}")
        for obs in payload.get("observations", []):
            logger.info(f"👁 Наблюдение: {obs}")

        if len(steps) < 3:
            logger.warning(
                f"⚠️ GUI automation выполнила только {len(steps)} шагов. "
                f"Возможно, установка не прошла полностью."
            )

        for s in steps:
            self.step(f"[VM] {s}")

        if status != "passed":
            local_log = f"reports/gui_log_fail_{int(time.time())}.log"
            try:
                self.winrm.download_file(self.log_path, local_log)
                self.attach(local_log)
            except Exception:
                pass
            self.fail(f"GUI automation завершилась со статусом {status}: {error}")

        self.step(f"GUI automation успешно завершила установку за {elapsed_time:.1f} сек")


class TestGuiWizardWalkthrough(TestGuiInstallation):
    """Полный обход мастера установки с проверками и реальной установкой"""
    test_id = "WIN-GUI-WALKTHROUGH-001"
    name = "Полный обход мастера установки с проверками и установкой"

    def __init__(self, **kwargs):
        kwargs["mode"] = "wizard_walkthrough"
        super().__init__(**kwargs)