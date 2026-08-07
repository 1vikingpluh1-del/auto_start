"""
Сервис для скачивания сборок (артефактов) с TeamCity.
Использует TeamCity REST API.
"""
import os
import fnmatch
from pathlib import Path
from typing import Optional
import requests
from requests.auth import HTTPBasicAuth

from Auto_create_vm.logger_setup import get_logger

logger = get_logger("TeamCityDownloader")


class TeamCityDownloader:
    """
    Клиент для взаимодействия с TeamCity REST API.
    Скачивает последний успешный артефакт по заданному паттерну.
    """

    def __init__(self):
        self.tc_url = os.getenv("TC_URL", "").rstrip("/")
        self.tc_user = os.getenv("TC_USER", "")
        self.tc_password = os.getenv("TC_PASSWORD", "")
        self.build_type = os.getenv("TC_BUILD_TYPE", "")
        self.branch = os.getenv("TC_BRANCH", "")
        self.artifact_pattern = os.getenv("TC_ARTIFACT_PATTERN", "*")

        if not all([self.tc_url, self.tc_user, self.tc_password, self.build_type]):
            raise ValueError(
                "Не заполнены обязательные переменные окружения TeamCity: "
                "TC_URL, TC_USER, TC_PASSWORD, TC_BUILD_TYPE"
            )

        self.session = requests.Session()
        self.session.auth = HTTPBasicAuth(self.tc_user, self.tc_password)
        self.session.headers.update({"Accept": "application/json"})
        # Отключаем предупреждения о самоподписанных сертификатах, если нужно
        self.session.verify = False
        # Не подхватывать системный прокси из Windows (proxy.bolid.ru мёртвый)
        self.session.trust_env = False
        self.session.proxies = {}
        # Подавляем InsecureRequestWarning
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def _api_url(self, endpoint: str) -> str:
        return f"{self.tc_url}/app/rest/{endpoint}"

    def get_latest_build_id(self) -> Optional[int]:
        """
        Получить ID последнего успешного билда по buildType и branch.
        """
        logger.info(
            f"Поиск последнего успешного билда: type={self.build_type}, branch={self.branch}"
        )
        locator = f"buildType:{self.build_type},branch:{self.branch},status:success,count:1"
        url = self._api_url(f"builds?locator={locator}&fields=build(id)")

        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            data = response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка при запросе к TeamCity: {e}")
            raise

        builds = data.get("build", [])
        if not builds:
            raise ValueError(
                f"Не найдено успешных билдов для {self.build_type} в ветке {self.branch}"
            )
        # TeamCity REST API всегда возвращает массив, даже если count=1
        build_id = builds[0]["id"]
        logger.info(f"Найден билд ID: {build_id}")
        return build_id

    def get_artifacts_list(self, build_id: int) -> list:
        """
        Получить список артефактов билда.
        Возвращает список словарей: [{"name": "file.exe", "href": "..."}, ...]
        """
        url = self._api_url(f"builds/id:{build_id}/artifacts/children")
        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            return response.json().get("file", [])
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка при получении списка артефактов: {e}")
            raise

    def find_artifact(self, build_id: int) -> Optional[str]:
        """
        Найти артефакт по паттерну (например, *.exe) в списке артефактов билда.
        Возвращает имя файла или None.
        """
        artifacts = self.get_artifacts_list(build_id)
        logger.info(f"Найдено артефактов: {len(artifacts)}. Ищем по паттерну: {self.artifact_pattern}")

        for artifact in artifacts:
            name = artifact.get("name", "")
            if fnmatch.fnmatch(name, self.artifact_pattern):
                logger.info(f"Найден подходящий артефакт: {name}")
                return name

        raise ValueError(
            f"Артефакт по паттерну '{self.artifact_pattern}' не найден в билде {build_id}. "
            f"Доступные файлы: {[a.get('name') for a in artifacts]}"
        )

    def download_artifact(self, build_id: int, artifact_name: str, local_dir: str) -> str:
        """
        Скачать артефакт из билда в локальную папку.
        Возвращает полный путь к скачанному файлу.
        """
        url = self._api_url(f"builds/id:{build_id}/artifacts/content/{artifact_name}")
        local_path = Path(local_dir) / artifact_name
        local_path.parent.mkdir(parents=True, exist_ok=True)

        logger.info(f"Скачивание {artifact_name} -> {local_path}")
        try:
            # stream=True для больших файлов
            with self.session.get(url, stream=True, timeout=60) as response:
                response.raise_for_status()
                with open(local_path, "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка при скачивании артефакта: {e}")
            raise

        size_mb = local_path.stat().st_size / (1024 * 1024)
        logger.info(f"✓ Артефакт скачан: {local_path} ({size_mb:.2f} МБ)")
        return str(local_path)

    def download_latest(self, local_dir: str = "downloads") -> str:
        """
        Главный метод: найти последний успешный билд и скачать нужный артефакт.
        Возвращает путь к скачанному файлу.
        """
        build_id = self.get_latest_build_id()
        artifact_name = self.find_artifact(build_id)
        return self.download_artifact(build_id, artifact_name, local_dir)


# Пример использования (для тестирования отдельно)
if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    downloader = TeamCityDownloader()
    try:
        path = downloader.download_latest(local_dir="test_downloads")
        print(f"Готово! Файл сохранен: {path}")
    except Exception as e:
        print(f"Ошибка: {e}")