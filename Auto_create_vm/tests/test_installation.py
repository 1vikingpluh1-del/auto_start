"""
Тест установки ПО на Windows VM
"""
from Auto_create_vm.tests.base_tests import BaseTest, TestResult, Status
from Auto_create_vm.utils.winrm_client import WinRMClient
from Auto_create_vm.logger_setup import get_logger

logger = get_logger("TestInstallation")

class TestInstallation(BaseTest):
    test_id = "WIN-INSTALL-001"
    name = "Проверка установки приложения на Windows"

    def __init__(self, winrm_client: WinRMClient,
                 installer_name: str = "OrionProSetup",
                 install_path: str = r"C:\BOLID\ARM_ORION_PRO_2_0_1"):
        """
        :param winrm_client: Клиент для управления VM
        :param installer_name: Имя процесса установщика (без .exe)
        :param install_path: Путь, куда установлено ПО
        """
        super().__init__()
        self.winrm_client = winrm_client
        self.installer_name = installer_name
        self.install_path = install_path

    def execute(self, **context):
        """
        Проверка, установлено ли приложение
        """
        # ШАГ 1: Убеждаемся, что процесс установки больше не активен
        self.step(f"Проверка отсутствия запущенного процесса установщика: {self.installer_name}")
        is_installer_running = self.winrm_client.is_process_running(self.installer_name)

        if is_installer_running:
            self.fail(f"Установщик {self.installer_name} всё ещё запущен! "
                      f"Возможно, установка зависла или требует вмешательства.")

        self.step("Процесс установщика не найден (установка завершена)")

        # ШАГ 2: Проверяем наличие ключевых файлов
        # Используем wildcard, так как точное имя exe может меняться от версии
        check_cmd = f"Test-Path '{self.install_path}\\*.exe'"

        self.step(f"Проверка наличия исполняемых файлов в {self.install_path}")
        code, out, err = self.winrm_client.run_ps(check_cmd, check=False)

        if out.strip().lower() != "true":
            self.fail(f"В папке {self.install_path} не найдено ни одного .exe файла. "
                      f"Установка прошла неудачно или путь указан неверно. Вывод PS: {out}")

        self.step(f"Приложение успешно обнаружено в {self.install_path}")
        # Возвращать ничего не нужно, BaseTest.run() сам поставит статус PASSED, если нет исключений