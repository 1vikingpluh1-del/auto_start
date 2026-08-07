"""
Здесь хранятся все настройки. В будущем пароли отсюда лучше вынести в переменные окружения.
upd. пароли вынесены в отдельную переменную .env
"""

import os
from dotenv import load_dotenv

# КРИТИЧЕСКИ ВАЖНО: Загружаем .env СРАЗУ при импорте этого модуля.
# Это гарантирует, что os.getenv() вернет реальные значения,
# независимо от того, какой скрипт импортирует config.py.
load_dotenv()

class Config:
    # === Подключение ===
    VCENTER_HOST = os.getenv("VC_HOST", "your_vcenter_host_here")
    VCENTER_PORT = int(os.getenv("VC_PORT", 443))
    VCENTER_USER = os.getenv("VC_USER", "your_user_here")
    VCENTER_PASSWORD = os.getenv("VC_PASSWORD")

    # === Прокси ===
    PROXY_URL = os.getenv("PROXY_URL", "")
    PROXY_PORT = os.getenv("PROXY_PORT", "3128")
    PROXY_USER = os.getenv("PROXY_USER", "")
    PROXY_PASSWORD = os.getenv("PROXY_PASSWORD", "")

    # === Имена и названия вм ===
    #TEMPLATE_NAME = "Name_1_Win_10_x64_22H2"
    DATACENTER_NAME = "SEELE"
    DATASTORE_NAME = "Disk_7-18tb"
    #NEW_VM_NAME = os.getenv("NEW_VM_NAME","test_win10_001")
    FOLDER_NAME = "AUTO_TEST_VM"

    # === таймауты ===
    VM_TOOLS_WAIT_SEC = 60
    MAX_IP_WAIT_SEC = 600
    POLL_INTERVAL_SEC = 5

# Список виртуальных машин для создания
VM_CONFIG = [
    {
        "template": "Auto_1_Win_10_x64_22H2",
        "new_name": "test_win10_001",
        "os_type": "windows"
    },
    #{
     #   "template": "Name_1_Astra_Smolensk_1.8.1.6_CORE_6.1_01.08.2025",
      #  "new_name": "test_astra_001",
       # "os_type": "linux"
    #}
]