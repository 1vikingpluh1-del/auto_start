"""
Точка входа. Только управление потоком выполнения.
"""

from dotenv import load_dotenv
load_dotenv()  # Загружает переменные из файла .env

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from Auto_create_vm.logger_setup import get_logger
from Auto_create_vm.vcenter_manager import VCenterManager
from Auto_create_vm.config import VM_CONFIG

logger = get_logger("Main")

def create_single_vm_task(manager: VCenterManager, vm_cfg: dict) -> dict:
    """Метод для создания одной ВМ в отдельном потоке"""
    # ИСПРАВЛЕНО: нормализуем os_type (убираем пробелы и приводим к lower),
    # чтобы сравнение == "windows" точно сработало.
    result = {
        "name": str(vm_cfg.get("new_name", "")).strip(),
        "os_type": str(vm_cfg.get("os_type", "unknown")).strip().lower(),
        "ip": None,
        "error": None
    }
    try:
        logger.info(f"{'=' * 50}")
        logger.info(f"создаем: {vm_cfg['new_name']} (в отдельном потоке)")
        logger.info(f"{'=' * 50}")

        ip = manager.create_vm_from_template(
            template_name=vm_cfg["template"],
            new_vm_name=vm_cfg["new_name"]
        )
        result["ip"] = ip
        return result

    except Exception as e:
        result["error"] = str(e)
        logger.error(f"Ошибка при создании {vm_cfg['new_name']}: {e}")
        return result

def main():
    if len(sys.argv) > 1:
        command = sys.argv[1]

        if command == "list-pools":
            manager = VCenterManager()
            manager.list_resource_pools()
        elif command == "parallel":
            run_parallel(max_workers=2) # вернул на parallel
            # параллельное создание
            # run_parallel(max_workers=2) # скорее всего этот ограничитель который надо будет убирать
        elif command == "sequential":
            run_sequential()
        else:
            print(f"Неизвестная команда: {command}")
            print("Доступные команды:")
            print("  python main.py            # Создать ВМ последовательно")
            print("  python main.py parallel   # Создать ВМ параллельно")
            print("  python main.py list-pools # Показать все Resource Pool")
    else:
        # параллельное создание по умолчанию
        logger.info("Режим не указан. Запуск в последовательном режиме (sequential).")
        run_sequential()

def run_sequential():
    """Последовательное создание(резервный режим)"""
    logger.info("запуск последовательного создания")
    manager = VCenterManager()

    for vm_cfg in VM_CONFIG:
        result = create_single_vm_task(manager, vm_cfg)
        if result["ip"]:
            print(f"\n {result['name']} полностью готова. IP: {result['ip']}\n")
        # === ДОБАВЛЕН БЛОК: ПРОВЕРКА ТИПА ОС И ЗАПУСК ТЕСТОВ ===
            if result.get("os_type") == "windows":
                logger.info(f" Запуск сценария тестирования для Windows ВМ: {result['name']}")
                try:
                    from Auto_create_vm.scenario.scenario_win_install import run_tests_for_vm
                    run_tests_for_vm(
                        vm_name=result["name"],
                        vm_ip=result["ip"],
                        installer_name="setup",
                        install_path=r"C:\BOLID\ARM_ORION_PRO_2_0_1"
                    )
                except Exception as e:
                    logger.error(f"Ошибка тестирования {result['name']}: {e}")
            else:
                logger.info(f"Пропускаем тесты для {result['name']} (OS: {result.get('os_type')})")
        else:
            print(f"\n {result['name']} создана, но IP не получен.\n")

    logger.info("Все задачи по созданию ВМ завершены!")


def run_parallel(max_workers: int = 2):
    """Основной метод создания виртуальных машин в параллели"""
    logger.info(f"Запускаем параллельное создание ({max_workers} потока)")
    logger.info(f"Всего ВМ для создания: {len(VM_CONFIG)}")

    manager = VCenterManager()
    results = []

    try:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_vm = {
                executor.submit(create_single_vm_task, manager, vm_cfg): vm_cfg
                for vm_cfg in VM_CONFIG
            }

            for future in as_completed(future_to_vm):
                result = future.result()
                results.append(result)
                if result["ip"]:
                    print(f"\n {result['name']} готова. IP: {result['ip']}\n")
                else:
                    print(f"\n {result['name']} создана, но IP не получен.\n")

        success = sum(1 for r in results if r["ip"])
        failed = len(results) - success
        logger.info("=" * 50)
        logger.info(f"Итоги: Успешно {success}, неудач {failed}")
        logger.info("=" * 50)

        # === Запуск сценария тестирования ТОЛЬКО для Windows ВМ ===
        if success > 0:
            logger.info("Проверка необходимости запуска тестов...")
            from Auto_create_vm.scenario.scenario_win_install import run_tests_for_vm

            for result in results:
                if result["ip"]:
                    # ПРОВЕРЯЕМ ТИП ОС
                    if result.get("os_type") == "windows":
                        logger.info(f"Запуск сценария тестирования для Windows ВМ: {result['name']}")
                        try:
                            run_tests_for_vm(
                                vm_name=result["name"],
                                vm_ip=result["ip"],
                                installer_name="setup",
                                install_path=r"C:\BOLID\ARM_ORION_PRO_2_0_1"
                            )
                        except Exception as e:
                            logger.error(f"Ошибка тестирования {result['name']}: {e}")
                    else:
                        logger.info(f"⏭ Пропускаем тесты для {result['name']} (OS: {result.get('os_type')})")

    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()