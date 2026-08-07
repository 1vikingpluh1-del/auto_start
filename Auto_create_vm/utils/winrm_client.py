"""
WinRM-клиент для удаленного выполнения PowerShell команд для Windows-VM
"""
import os
import time
from typing import Optional, Tuple
from Auto_create_vm.logger_setup import get_logger

logger = get_logger("WinRMClient")

try:
    import winrm
except ImportError:
    winrm = None
    logger.warning("pywinrm не установлен. Необходимо выполнить команду pip install pywinrm")


class WinRMClient:
    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        port: int = 5985,
        transport: str = "ntlm",
        ssl: bool = False,
        timeout: int = 60,
    ):
        if winrm is None:
            raise RuntimeError("pywinrm не установлен. Необходимо выполнить команду pip install pywinrm")
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.transport = transport
        self.timeout = timeout
        self.endpoint = f"{'https' if ssl else 'http'}://{host}:{port}/wsman"
        self._session: Optional[winrm.Session] = None

    def connect(self, retries: int = 15, delay: int = 10) -> None:
        """Подключение к ВМ с повторами, с жестким отключением прокси"""
        last_err = None
        for attempt in range(1, retries + 1):
            try:
                logger.info(
                    f"WinRM: подключение к {self.host}:{self.port} "
                    f"(попытка {attempt}/{retries})"
                )
                session = winrm.Session(
                    self.endpoint,
                    auth=(self.username, self.password),
                    transport=self.transport,
                    server_cert_validation="ignore",
                    operation_timeout_sec=self.timeout,
                    read_timeout_sec=self.timeout * 2,
                )

                # === КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: отключаем прокси для WinRM ===
                try:
                    req_session = session.protocol.transport.session
                    req_session.trust_env = False  # Игнорируем системные настройки прокси
                    # WinRM — внутренний протокол, прокси никогда не нужен
                    req_session.proxies = {'http': None, 'https': None}
                    logger.info(f"WinRM: прокси отключен (trust_env=False, proxies=None)")
                except AttributeError:
                    logger.warning(
                        "WinRM: не удалось получить доступ к requests.Session, "
                        "используем fallback через os.environ"
                    )
                    os.environ['NO_PROXY'] = '*'
                    os.environ['no_proxy'] = '*'

                # Тестовый запрос
                session.run_ps("Write-Host 'OK'")
                self._session = session
                logger.info("WinRM: подключено")
                return
            except Exception as e:
                last_err = e
                logger.debug(f"WinRM: не удалось подключиться: {e}")
                time.sleep(delay)
        raise ConnectionError(
            f"Не удалось подключиться к {self.host} по WinRM за {retries} попыток. "
            f"Последняя ошибка: {last_err}"
        )

    def close(self) -> None:
        self._session = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def _ensure_connected(self):
        if not self._session:
            raise RuntimeError("WinRM не подключен. Вызовите connect() сначала")

    @staticmethod
    def _decode_stream(data) -> str:
        """Умное декодирование: UTF-16LE только если есть нулевые байты"""
        if data is None:
            return ""
        if isinstance(data, str):
            return data
        if not isinstance(data, bytes) or len(data) == 0:
            return str(data)
        # UTF-16LE детектируем по нулевым байтам
        if b"\x00" in data[:4096]:
            for enc in ("utf-16-le", "utf-16", "utf-8"):
                try:
                    return data.decode(enc)
                except UnicodeDecodeError:
                    pass
        # Обычно вывод PowerShell в UTF-8 или cp1251
        for enc in ("utf-8", "cp1251", "utf-16-le"):
            try:
                return data.decode(enc)
            except UnicodeDecodeError:
                pass
        return data.decode("utf-8", errors="replace")

    def run_ps(self, script: str, timeout: int = 300, check: bool = True) -> Tuple[int, str, str]:
        """Выполнить PowerShell скрипт. Возвращает exit_code, stdout, stderr"""
        self._ensure_connected()
        logger.debug(f"WinRM[{self.host}] PS> {script[:120]}")
        try:
            r = self._session.run_ps(script)
        except Exception as e:
            if check:
                raise
            return (-1, "", str(e))

        out = self._decode_stream(r.std_out).strip()
        err = self._decode_stream(r.std_err).strip()

        if check and r.status_code != 0:
            raise RuntimeError(f"PowerShell вернул код {r.status_code}:\n{err or out}")
        return r.status_code, out, err

    def run_cmd(self, cmd: str, timeout: int = 300, check: bool = True) -> Tuple[int, str, str]:
        """Выполнить CMD команду"""
        self._ensure_connected()
        r = self._session.run_cmd(cmd)
        out = r.std_out.decode("utf-8", errors="replace").strip()
        err = r.std_err.decode("utf-8", errors="replace").strip()
        if check and r.status_code != 0:
            raise RuntimeError(f"cmd вернул код {r.status_code}:\n{err or out}")
        return r.status_code, out, err

    def upload_files(self, local_path: str, remote_path: str) -> None:
        """Загрузить файл на ВМ через Base64 чанки"""
        if not os.path.exists(local_path):
            raise FileNotFoundError(local_path)
        size = os.path.getsize(local_path)
        logger.info(f"WinRM: загрузка {local_path} -> {remote_path} ({size} байт)")
        import base64
        with open(local_path, "rb") as f:
            data = base64.b64encode(f.read()).decode("ascii")
        self.run_ps(f"New-Item -Path '{remote_path}' -ItemType File -Force | Out-Null")
        chunk_size = 4000
        for i in range(0, len(data), chunk_size):
            chunk = data[i : i + chunk_size]
            self.run_ps(f"[IO.File]::AppendAllText('{remote_path}','{chunk}')")
        self.run_ps(
            f"$b64 = Get-Content '{remote_path}' -Raw; "
            f"[IO.File]::WriteAllBytes('{remote_path}', [Convert]::FromBase64String($b64))"
        )
        logger.info("WinRM: файл загружен")

    def download_file(self, remote_path: str, local_path: str, max_size_mb: int = 50) -> None:
        """Скачать файл с ВМ"""
        import base64
        code, out, _ = self.run_ps(
            f"(Get-Item -LiteralPath '{remote_path}' -ErrorAction Stop).Length",
            check=False,
        )
        if code != 0:
            raise FileNotFoundError(f"файл не найден на ВМ: {remote_path}")
        try:
            file_size_bytes = int(out.strip())
        except (ValueError, AttributeError):
            raise ValueError(f"не удалось получить размер файла {remote_path}")
        if file_size_bytes > max_size_mb * 1024 * 1024:
            raise ValueError(
                f"файл {remote_path} слишком большой "
                f"({file_size_bytes / 1024 / 1024:.1f} МБ > {max_size_mb} МБ). "
                f"WinRM не предназначен для передачи больших файлов"
            )
        logger.info(f"WinRM: скачивание {remote_path} -> {local_path} ({file_size_bytes})")
        _, b64, _ = self.run_ps(
            f"[Convert]::ToBase64String([IO.File]::ReadAllBytes('{remote_path}'))"
        )
        os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
        with open(local_path, "wb") as f:
            f.write(base64.b64decode(b64))
        logger.info(f"WinRM: скачано {remote_path} -> {local_path}")

    def run_interactive(
        self,
        exe_path: str,
        arguments: str = "",
        user: Optional[str] = None,
        task_name: str = "AutoTestTask",
    ) -> None:
        """Запустить .exe в ИНТЕРАКТИВНОЙ сессии пользователя через Scheduled Task (для GUI)"""
        self._ensure_connected()
        self.run_ps(
            f"Unregister-ScheduledTask -TaskName '{task_name}' -Confirm:$false -ErrorAction SilentlyContinue",
            check=False,
        )
        safe_exe = exe_path.replace("'", "''")
        safe_args = arguments.replace("'", "''") if arguments else ""
        target_user = user or self.username
        safe_user = target_user.replace("'", "''")

        # === ИСПРАВЛЕНИЕ: если arguments пустой, не передаём -Argument вообще ===
        if safe_args:
            action_line = f"$action = New-ScheduledTaskAction -Execute '{safe_exe}' -Argument '{safe_args}'"
        else:
            action_line = f"$action = New-ScheduledTaskAction -Execute '{safe_exe}'"

        script = f"""
{action_line}
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddSeconds(2)
$principal = New-ScheduledTaskPrincipal -UserId '{safe_user}' -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName '{task_name}' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Start-Sleep -Seconds 2
Start-ScheduledTask -TaskName '{task_name}'
"""
        logger.info(f"WinRM: интерактивный запуск {exe_path} через ScheduledTask")
        self.run_ps(script)
        logger.info("WinRM: задача запущена ✓ (GUI должен появиться на экране ВМ)")

    def is_process_running(self, process_name: str) -> bool:
        """Проверить, запущен ли процесс на ВМ."""
        code, out, _ = self.run_ps(
            f"Get-Process -Name '{process_name}' -ErrorAction SilentlyContinue | Select-Object -First 1",
            check=False,
        )
        return bool(out.strip())