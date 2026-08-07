"""
Сервис для записи результатов тестов в JSON
"""
import json
import os
from datetime import datetime
from typing import List
from Auto_create_vm.tests.base_tests import TestResult, Status
from Auto_create_vm.logger_setup import get_logger

logger = get_logger("ResultRecorder")

class ResultRecorder:
    def __init__(self, report_dir: str = "reports"):
        self.report_dir = report_dir
        os.makedirs(self.report_dir, exist_ok=True)

    def save_results(self, results: List[TestResult], scenario_name: str = "default_scenario"):
        """
        Сохраняет результаты тестов в JSON файл
        """
        if not results:
            logger.warning("Нет результатов для сохранения")
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{scenario_name}_{timestamp}.json"
        filepath = os.path.join(self.report_dir, filename)

        report_data = {
            "scenario": scenario_name,
            "timestamp": timestamp,
            "total_tests": len(results),
            "passed": sum(1 for r in results if r.status == Status.PASSED),
            "failed": sum(1 for r in results if r.status == Status.FAILED),
            "blocked": sum(1 for r in results if r.status == Status.BLOCKED),
            "skipped": sum(1 for r in results if r.status == Status.SKIPPED),
            "tests": []
        }

        for res in results:
            test_entry = {
                "test_id": res.test_id,
                "name": res.name,
                "status": res.status.value,
                "duration_sec": res.duration_sec,
                "message": res.message,
                "steps": res.step_results,
                "attachments": res.attachments
            }
            report_data["tests"].append(test_entry)

        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(report_data, f, indent=4, ensure_ascii=False)
            logger.info(f"Результаты сохранены в {filepath}")
        except Exception as e:
            logger.error(f"Ошибка при сохранении отчета: {e}")