"""
Полный сценарий: существующая ВМ -> WinRM ->
ПК сам скачивает артефакт с TeamCity (Python+OpenSSL = TLS 1.3) ->
ПК раздаёт файл ВМ по локальному HTTP (без TLS, без прокси) ->
ГРАФИЧЕСКАЯ установка через pywinauto -> проверка
"""
import argparse
import functools
import http.server
import json
import os
import socket
import socketserver
import threading
import time
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv

load_dotenv()

from Auto_create_vm.utils.winrm_client import WinRMClient
from Auto_create_vm.tests.test_installation import TestInstallation
from Auto_create_vm.services.teamcity_downloader import TeamCityDownloader
from Auto_create_vm.logger_setup import get_logger

logger = get_logger("ScenarioWinInstall")

# === КОНСТАНТЫ ===
TEST_VM_IP = "192.168.202.212"
HTTP_PORT = 8000
INSTALL_BASE_PATH = r"C:\BOLID\ARM_ORION_PRO_2_0_1"
INSTALLER_WAIT_TIMEOUT = 1800  # 30 минут на GUI-установку

# Параметры GUI-автоматизации на ВМ (пути внутри Windows-ВМ)
VM_PYTHON_EXE = os.getenv("VM_PYTHON_EXE", r"C:\Python312\python.exe")
VM_GUI_RUNNER_PATH = os.getenv("VM_GUI_RUNNER_PATH", r"C:\AutoTest\gui_install_automation.py")
VM_GUI_RESULT_PATH = os.getenv("VM_GUI_RESULT_PATH", r"C:\Temp\gui_result.json")
VM_GUI_LOG_PATH = os.getenv("VM_GUI_LOG_PATH", r"C:\Temp\gui_install_automation.log")


try:
    from Auto_create_vm.services.result_recorder import ResultRecorder
except ImportError:
    class ResultRecorder:
        def save_results(self, results, scenario_name):
            logger.info(f"[ЗАГЛУШКА] Результаты '{scenario_name}' не сохранены")


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP-хэндлер без спама запросов в консоль"""
    def log_message(self, fmt, *args):
        pass


def _get_local_ip_for_vm(vm_ip: str) -> str:
    """IP этой машины в сети, доступной ВМ"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((vm_ip, 1))  # пакет реально не отправляется
        return s.getsockname()[0]
    except OSError:
        return socket.gethostbyname(socket.gethostname())
    finally:
        s.close()


def _start_local_http_server(directory: str, port: int = HTTP_PORT):
    """Поднять HTTP-сервер для раздачи установщика ВМ"""
    handler = functools.partial(_QuietHandler, directory=directory)
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.ThreadingTCPServer(("0.0.0.0", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    logger.info(f"✓ Локальный HTTP-сервер поднят на порту {port} (папка: {directory})")
    return httpd


def test_existing_vm(
    vm_ip: str,
    installer_name: str = "setup",
    install_path: str = INSTALL_BASE_PATH,
    vm_name: str = "",
):
    """Тестирование уже созданной ВМ (без клонирования)"""
    win_user = os.getenv("TEST_WIN_USER")
    win_password = os.getenv("TEST_WIN_PASSWORD")

    if not win_user or not win_password:
        logger.error("❌ КРИТИЧЕСКАЯ ОШИБКА: Заполните TEST_WIN_USER и TEST_WIN_PASSWORD в .env!")
        return

    recorder = ResultRecorder()
    results = []
    httpd = None

    try:
        # ==========================================================
        # ШАГ 0: ПК сам скачивает артефакт с TeamCity
        # ==========================================================
        logger.info("=== ШАГ 0: Скачивание артефакта с TeamCity на ПК ===")
        downloader = TeamCityDownloader()
        local_installer = downloader.download_latest(local_dir="downloads")
        filename = Path(local_installer).name
        directory = str(Path(local_installer).resolve().parent)

        local_ip = _get_local_ip_for_vm(vm_ip)
        httpd = _start_local_http_server(directory)
        logger.info(f"✓ ВМ заберёт файл по адресу: http://{local_ip}:{HTTP_PORT}/{filename}")

        # ==========================================================
        # ШАГ 1: ВМ скачивает установщик с ПК по HTTP
        # ==========================================================
        logger.info(f"=== ШАГ 1: Скачивание установщика на ВМ {vm_ip} по HTTP ===")
        with WinRMClient(host=vm_ip, username=win_user, password=win_password) as winrm:
            logger.info("✓ Подключение по WinRM успешно")

            # ИСПРАВЛЕНО: добавлен Unblock-File, иначе Windows может блокировать запуск скачанного exe
            ps_download_script = f"""
            New-Item -ItemType Directory -Force -Path "C:\\Temp" | Out-Null
            $url = "http://{local_ip}:{HTTP_PORT}/{quote(filename)}"
            $destPath = "C:\\Temp\\{filename}"
            Write-Host "Скачивание установщика: $url"
            $wc = New-Object System.Net.WebClient
            $wc.Proxy = [System.Net.GlobalProxySelection]::GetEmptyWebProxy()
            $wc.DownloadFile($url, $destPath)
            if (-not (Test-Path $destPath)) {{
                Write-Error "Файл не скачался: $destPath"
                exit 1
            }}
            Unblock-File -Path $destPath -ErrorAction SilentlyContinue
            $size = (Get-Item $destPath).Length
            Write-Host "✓ Скачивание завершено: $destPath ($size байт)"
            Write-Host "INSTALLER_PATH=$destPath"
            """
            code, out, err = winrm.run_ps(ps_download_script, check=False)
            if code != 0:
                raise RuntimeError(
                    f"Не удалось скачать установщик на ВМ.\nSTDOUT: {out}\nSTDERR: {err}"
                )

            installer_path_on_vm = None
            for line in out.split('\n'):
                if line.startswith("INSTALLER_PATH="):
                    installer_path_on_vm = line.split("=", 1)[1].strip()
                    break
            if not installer_path_on_vm:
                raise RuntimeError(f"Не удалось определить путь к установщику.\nSTDOUT: {out}")
            logger.info(f"✓ Установщик на ВМ: {installer_path_on_vm}")

            # ==========================================================
            # ШАГ 2: Запуск теста GUI-автоматизации установки
            # ==========================================================
            logger.info("=== ШАГ 2: Запуск теста GUI-автоматизации ===")
            from Auto_create_vm.tests.test_gui_installation import TestGuiWizardWalkthrough

            gui_test = TestGuiWizardWalkthrough(
                winrm_client=winrm,
                installer_path=installer_path_on_vm,
                install_path=install_path,
                user=win_user,
                python_exe=VM_PYTHON_EXE,
                runner_path=VM_GUI_RUNNER_PATH,
                result_path=VM_GUI_RESULT_PATH,
                log_path=VM_GUI_LOG_PATH,
                timeout_sec=3600,  # обход + реальная установка может идти долго
            )

            # Запускаем тест через BaseTest.run()
            gui_result = gui_test.run()
            results.append(gui_result)

            if gui_result.status.value != "passed":
                # Если GUI-тест упал, прерываем сценарий, нет смысла проверять файлы
                raise RuntimeError(f"Тест GUI-установки провален: {gui_result.message}")

        # ==========================================================
        # ШАГ 3: Проверка установки (тест WIN-INSTALL-001)
        # ==========================================================
        logger.info("=== ШАГ 3: Запуск тестов проверки установки ===")
        installer_process_name = Path(installer_path_on_vm).stem

        with WinRMClient(host=vm_ip, username=win_user, password=win_password) as winrm:
            test = TestInstallation(
                winrm_client=winrm,
                installer_name=installer_process_name,
                install_path=install_path,
            )
            result = test.run()
            results.append(result)

            if result.status.value == "passed":
                logger.info(f"✅ Тест [{result.test_id}] УСПЕШНО")
            else:
                logger.error(f"❌ Тест [{result.test_id}] FAILED: {result.message}")

    except Exception as e:
        logger.error(f"❌ Критическая ошибка в сценарии: {type(e).__name__}: {e}")
    finally:
        if httpd:
            try:
                httpd.shutdown()
            except Exception:
                pass
            try:
                httpd.server_close()
            except Exception:
                pass
            logger.info("Локальный HTTP-сервер остановлен")
        recorder.save_results(results, scenario_name="win_install_gui_existing_vm")
        logger.info("=== Сценарий завершен ===")


def run_tests_for_vm(
    vm_name: str,
    vm_ip: str,
    installer_name: str = "setup",
    install_path: str = INSTALL_BASE_PATH,
):
    """Точка входа для main.py (связка создание ВМ -> тесты)"""
    logger.info(f"🚀 Сценарий для {vm_name} ({vm_ip})")
    # ИСПРАВЛЕНО: параметры теперь реально передаются в test_existing_vm
    test_existing_vm(
        vm_ip=vm_ip,
        installer_name=installer_name,
        install_path=install_path,
        vm_name=vm_name,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Сценарий установки ПО на Windows VM")
    parser.add_argument("--ip", type=str,
                        help="IP адрес существующей ВМ (переопределяет TEST_VM_IP)")
    args = parser.parse_args()

    target_ip = args.ip or TEST_VM_IP
    logger.info(f"🔧 Режим: тестирование существующей ВМ {target_ip}")
    test_existing_vm(target_ip)