"""
Скрипт для ручной очистки тестовых ВМ и папки AUTO_TEST_VM в vCenter.
Запускать вручную: python cleanup_vms.py
"""
import sys
import os
from dotenv import load_dotenv

# ⚠️ САМОЕ ВАЖНОЕ: Загружаем .env ДО импорта любых наших модулей!
load_dotenv()

from pyVmomi import vim
from Auto_create_vm.vcenter_manager import VCenterManager
from Auto_create_vm.config import Config
from Auto_create_vm.logger_setup import get_logger

logger = get_logger("Cleanup")


def cleanup_test_vms():
    """
    Находит папку AUTO_TEST_VM, выключает и удаляет все ВМ внутри неё,
    а затем удаляет саму папку.
    """
    logger.info("Инициализация подключения к vCenter...")
    try:
        manager = VCenterManager()
        content = manager.content
    except Exception as e:
        logger.error(f"Не удалось подключиться к vCenter: {e}")
        sys.exit(1)

    # 1. Ищем папку
    logger.info(f"Поиск папки '{Config.FOLDER_NAME}' в Datacenter '{Config.DATACENTER_NAME}'...")
    folder = manager._find_obj([vim.Folder], Config.FOLDER_NAME)

    if not folder:
        logger.info(f" Папка '{Config.FOLDER_NAME}' не найдена. Очищать нечего.")
        return

    # 2. Находим все ВМ внутри этой папки
    vms_to_delete = []
    for child in folder.childEntity:
        if isinstance(child, vim.VirtualMachine):
            vms_to_delete.append(child)

    if not vms_to_delete:
        logger.info(" В папке нет виртуальных машин.")
    else:
        logger.info(f" Найдено ВМ для удаления: {len(vms_to_delete)}")

        for vm in vms_to_delete:
            #  КРИТИЧЕСКИ ВАЖНО: Сохраняем имя СРАЗУ, до любых операций с объектом!
            vm_name = vm.name
            logger.info(f"Обработка ВМ: {vm_name}")

            # Шаг А: Если ВМ включена, принудительно выключаем её
            if vm.runtime.powerState == vim.VirtualMachine.PowerState.poweredOn:
                logger.info(f"   Выключение {vm_name}...")
                try:
                    task = vm.PowerOffVM_Task()
                    manager._wait_for_task(task)
                    logger.info(f"   {vm_name} выключена")
                except Exception as e:
                    logger.error(f"  Ошибка при выключении {vm_name}: {e}")
                    continue  # Пропускаем удаление, если не удалось выключить

            # Шаг Б: Уничтожаем ВМ
            logger.info(f"  🗑 Уничтожение {vm_name}...")
            try:
                task = vm.Destroy_Task()
                manager._wait_for_task(task)
                logger.info(f"  {vm_name} успешно удалена")
            except Exception as e:
                # Если объект уже удален (например, другим процессом), это тоже считается успехом
                error_msg = str(e)
                if "ManagedObjectNotFound" in error_msg or "already been deleted" in error_msg:
                    logger.info(f"  {vm_name} уже была удалена или не найдена (это нормально)")
                else:
                    logger.error(f"  Ошибка при удалении {vm_name}: {e}")

    # 3. Пытаемся удалить саму папку
    logger.info(f"Попытка удаления папки '{Config.FOLDER_NAME}'...")
    try:
        task = folder.Destroy_Task()
        manager._wait_for_task(task)
        logger.info(f" Папка '{Config.FOLDER_NAME}' успешно удалена")
    except vim.fault.ResourceInUse:
        logger.warning(f" Не удалось удалить папку: она не пуста или используется другим процессом.")
    except Exception as e:
        # Если папка уже удалена, это тоже ок
        error_msg = str(e)
        if "ManagedObjectNotFound" in error_msg or "already been deleted" in error_msg:
            logger.info(f"  ℹПапка '{Config.FOLDER_NAME}' уже была удалена (это нормально)")
        else:
            logger.warning(f"⚠ Не удалось удалить папку: {e}")

    logger.info(" Процесс очистки завершен!")


if __name__ == "__main__":

    confirm = input("Вы уверены, что хотите продолжить? (введите 'да' или 'yes'): ").strip().lower()

    if confirm in ['да', 'yes', 'y']:
        print("\nЗапуск очистки...\n")
        cleanup_test_vms()
    else:
        print("\n Операция отменена пользователем.")
        sys.exit(0)