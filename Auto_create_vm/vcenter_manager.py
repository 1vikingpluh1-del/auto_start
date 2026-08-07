"""
Основной класс, инкапсулирующий всю работу с vCenter. Публичные методы - действия приватные _ - вспомогательные
"""

import atexit
import ssl
import time
from pyVim.connect import SmartConnect, Disconnect
from pyVmomi import vim
from .config import Config
from .logger_setup import get_logger

logger = get_logger("VCenterManager")

class VCenterManager:
    def __init__(self):
        self.content = self._connect()

    def _connect(self):
        """Подключение к vCenter"""
        context = ssl._create_unverified_context()  # ИСПРАВЛЕНО: опечатка в unverified
        logger.info(f"Подключение к {Config.VCENTER_HOST}...")
        try:
            si = SmartConnect(
                host=Config.VCENTER_HOST,
                user=Config.VCENTER_USER,
                pwd=Config.VCENTER_PASSWORD,
                port=Config.VCENTER_PORT,
                sslContext=context
            )
            atexit.register(Disconnect, si)
            logger.info("Успешно подключено к vCenter")
            return si.RetrieveContent()
        except Exception as e:
            logger.error(f"Ошибка подключения: {e}")
            raise

    def _find_obj(self, vimtype, name):
        """Поиск объекта по имени"""
        container = self.content.viewManager.CreateContainerView(
            self.content.rootFolder, vimtype, True
        )
        for obj in container.view:
            if obj.name == name:
                return obj
        return None

    def _wait_for_task(self,task):
        """Ожидание завершения задачи"""
        while task.info.state not in [vim.TaskInfo.State.success, vim.TaskInfo.State.error]:
            time.sleep(2)
        if task.info.state == vim.TaskInfo.State.error:
            raise RuntimeError(f"Задача упала: {task.info.error.msg}")

    def get_vm_ip(self, vm_name: str) -> str | None:
        """получение IP через PropertyCollector"""
        vm = self._find_obj([vim.VirtualMachine], vm_name)
        if not vm:
            return None

        # Явно запрашиваем свойства guest.ipAddress и guest.net
        # так как обычные view yне загружают их по умолчанию

        obj_spec = vim.PropertyCollector.ObjectSpec(obj=vm)
        prop_spec = vim.PropertyCollector.PropertySpec(
            type=vim.VirtualMachine,
            pathSet=["guest.ipAddress", "guest.net"]
        )
        filter_spec = vim.PropertyCollector.FilterSpec(
            objectSet=[obj_spec],
            propSet=[prop_spec]
        )

        try:
            result = self.content.propertyCollector.RetrieveProperties(specSet=[filter_spec])
            if result and result[0].propSet:
                for prop in result[0].propSet:
                    # Способ первый: Прямое свойство ipAddress
                    if prop.name == "guest.ipAddress" and prop.val:
                        return prop.val
                    # Способ 2: сли прямое свойство пустое, ищем в массиве сетевых интерфейсов
                    elif prop.name == "guest.net" and prop.val:
                        for net in prop.val:
                            if net.ipAddress:
                                for addr in net.ipAddress:
                                    if ":" not in addr:  # Фильтруем и берем ток Ipv4
                                        return addr
        except Exception as e:
            logger.debug(f"Временная ошибка при чтении свойств ВМ: {e}")
        return None

    def create_vm_from_template(self, template_name: str, new_vm_name: str) -> str | None:
        """Основная логика создания ВМ из Template."""

        # 1. Найти Template (используем переданный аргумент напрямую)
        template = self._find_obj([vim.VirtualMachine], template_name)
        if not template:
            raise ValueError(f"Template '{template_name}' не найден!")

        # Найти Datacenter
        datacenter = self._find_obj([vim.Datacenter], Config.DATACENTER_NAME)
        if not datacenter:
            raise ValueError(f"Datacenter '{Config.DATACENTER_NAME}' не найден!")

        # Найти Datastore
        datastore = self._find_obj([vim.Datastore], Config.DATASTORE_NAME)
        if not datastore:
            raise ValueError(f"Datastore '{Config.DATASTORE_NAME}' не найден!")

        # 2. Работа с папкой (исправленная потокобезопасная версия)
        folder = datacenter.vmFolder

        # Сначала пытаемся найти папку среди дочерних элементов
        folder_obj = next((child for child in folder.childEntity if getattr(child, "name", None) == Config.FOLDER_NAME),
                          None)

        if not folder_obj:
            try:
                logger.info(f"Создание папки '{Config.FOLDER_NAME}'...")
                folder_obj = folder.CreateFolder(name=Config.FOLDER_NAME)
            except vim.fault.DuplicateName:
                # Другой поток опередил нас и создал папку. Просто ищем её снова.
                logger.info(f"Папка '{Config.FOLDER_NAME}' уже создана другим потоком, используем её")
                folder_obj = next(
                    (child for child in folder.childEntity if getattr(child, "name", None) == Config.FOLDER_NAME), None)

                # Если вдруг её всё ещё нет в childEntity, используем наш надежный метод поиска по всему датацентру
                if not folder_obj:
                    folder_obj = self._find_obj([vim.Folder], Config.FOLDER_NAME)

        if not folder_obj:
            raise RuntimeError(f"Критическая ошибка: не удалось получить или создать папку '{Config.FOLDER_NAME}'")

        logger.info(f"Работа с папкой '{Config.FOLDER_NAME}' успешно инициализирована")

        # 3. Работа с Resource Pool
        resource_pool = self._find_obj([vim.ResourcePool], "Resources")
        if not resource_pool:
            logger.warning("Пул 'Resources' не найден, берем первый доступный")
            container = self.content.viewManager.CreateContainerView(self.content.rootFolder, [vim.ResourcePool], True)
            resource_pool = container.view[0] if container.view else None

        if not resource_pool:
            raise ValueError("Resource Pool не найден!")

        # 4. Клонирование
        relospec = vim.vm.RelocateSpec()
        relospec.datastore = datastore
        relospec.pool = resource_pool

        clonespec = vim.vm.CloneSpec()
        clonespec.location = relospec
        clonespec.powerOn = True
        clonespec.template = False

        logger.info(f"Запуск клонирования '{template_name}' -> '{new_vm_name}'...")
        task = template.Clone(folder=folder_obj, name=new_vm_name, spec=clonespec)
        logger.info("Задача клонирования создана, ожидание завершения...")
        self._wait_for_task(task)
        logger.info(f"✓ ВМ '{new_vm_name}' создана")

        # 5. Ждать IP-адрес
        logger.info(
            f"Ожидание IP-адреса для '{new_vm_name}' и инициализации VMware Tools (до {Config.MAX_IP_WAIT_SEC} сек)...")
        time.sleep(Config.VM_TOOLS_WAIT_SEC)

        start_time = time.time()
        while time.time() - start_time < Config.MAX_IP_WAIT_SEC:
            ip = self.get_vm_ip(new_vm_name)
            if ip:
                logger.info(f"✓ IP успешно получен для {new_vm_name}: {ip}")
                return ip

            elapsed = int(time.time() - start_time)
            if elapsed % 30 == 0 and elapsed > 0:
                logger.info(f"  ... ожидание сети для {new_vm_name} ({elapsed}/{Config.MAX_IP_WAIT_SEC} сек)")
            time.sleep(Config.POLL_INTERVAL_SEC)

        logger.error(f"Не удалось получить IP для {new_vm_name} через API.")
        logger.info("💡 Убедитесь, что в шаблоне установлены и запущены VMware Tools (или open-vm-tools для Astra).")
        return None

    def list_resource_pools(self):
        """вывод списка всех resource Pool"""
        container = self.content.viewManager.CreateContainerView(
            self.content.rootFolder, [vim.ResourcePool], True
        )
        print("\n=== Список всех Resource Pool в vCenter ===")
        if not container.view:
            print("  (Список пуст)")
        for pool in container.view:
            print(f" - {pool.name}")
        print("=" * 45 + "\n")