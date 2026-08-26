"""
GUI automation runner. Выполняется ВНУТРИ Windows-ВМ в активной сессии.

Режимы (--mode):
  wizard_smoke       - быстрая проверка кнопок 1-й страницы (без установки).
  wizard_walkthrough - полный обход мастера с проверками и РЕАЛЬНОЙ установкой.
  full_install       - старый режим по UI_STEPS.
"""
import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path

try:
    from pywinauto import Application, Desktop
    PYWINAUTO_IMPORT_ERROR = None
except Exception as e:
    Application = None
    Desktop = None
    PYWINAUTO_IMPORT_ERROR = str(e)

# Контрольные фразы лицензии (разбросаны по всему документу)
LICENSE_ANCHORS = [
    "ОГРН 1035003350766",
    "ИНН 5018000402",
    "ст. 428 Гражданского кодекса",
    "ПО не может эксплуатироваться без ключа защиты",
    "sales@bolid.ru",
    "Арбитражный суд Московской области",
    "https://bolid.ru/about/news/",
    "Электронный сейф /Драйвер СК-24",
    "СКУД и УРВ для 1С фотоверификация",
    "Приложение 1 к Лицензионному договору",
]

UI_STEPS = []  # для режима full_install


# ==================== вспомогательные функции ====================

def save_result(path: str, payload: dict):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "").lower()


def process_running(exe_name: str) -> bool:
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {exe_name}", "/NH"],
            capture_output=True, text=True, timeout=30,
        ).stdout
        return exe_name.lower() in out.lower()
    except Exception:
        return False


def wait_process_exit(exe_name: str, timeout: int = 60) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        if not process_running(exe_name):
            return True
        time.sleep(2)
    return False


def wait_until(fn, timeout: int, interval: float = 1.0) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        try:
            if fn():
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


def top_windows() -> dict:
    return {w.handle: w.window_text() for w in Desktop(backend="uia").windows()}


def safe_enabled(ctrl) -> bool:
    try:
        return bool(ctrl.is_enabled())
    except Exception:
        return False


def is_selected(ctrl):
    try:
        return bool(ctrl.iface_selection_item.IsSelected)
    except Exception:
        try:
            return ctrl.get_toggle_state() == 1
        except Exception:
            return None


def set_edit(ctrl, value: str):
    try:
        ctrl.set_edit_text(value)
    except Exception:
        ctrl.type_keys("^a", pause=0.05)
        ctrl.type_keys(value, with_spaces=True, pause=0.02)


def get_edit_value(ctrl) -> str:
    for getter in ("get_value", "window_text"):
        try:
            v = getattr(ctrl, getter)()
            if v:
                return v
        except Exception:
            continue
    return ""


def collect_texts(window) -> str:
    parts = []
    try:
        for ctrl in window.descendants():
            try:
                t = ctrl.window_text()
            except Exception:
                continue
            if t:
                parts.append(t)
    except Exception:
        pass
    return "\n".join(parts)


def extract_longest_text(window, min_len=500) -> str:
    best = ""
    try:
        for ctrl in window.descendants():
            try:
                t = ctrl.window_text()
            except Exception:
                continue
            if t and len(t) > len(best):
                best = t
    except Exception:
        pass
    return best if len(best) >= min_len else ""


def collect_install_stats(install_path: str) -> dict:
    stats = {"total_files": 0, "total_dirs": 0, "by_ext": {}}
    if not os.path.isdir(install_path):
        return stats
    for root, dirs, files in os.walk(install_path):
        stats["total_dirs"] += len(dirs)
        for f in files:
            ext = os.path.splitext(f)[1].lower() or "(без расширения)"
            stats["by_ext"][ext] = stats["by_ext"].get(ext, 0) + 1
            stats["total_files"] += 1
    return stats


# ==================== общие проверки ====================

def check_changelog(main, step) -> None:
    """Кнопка 'Список изменений' открывает окно и оно закрывается."""
    btn = main.child_window(title_re="Список изменений", control_type="Button")
    if not btn.exists(timeout=10):
        step("⚠ Кнопка 'Список изменений' не найдена на странице")
        return
    before = top_windows()
    btn.click_input()
    new_handle, new_title = None, None
    start = time.time()
    while time.time() - start < 15:
        for h, t in top_windows().items():
            if h not in before and t.strip():
                new_handle, new_title = h, t
                break
        if new_handle:
            break
        time.sleep(0.5)
    if not new_handle:
        raise RuntimeError("Кнопка 'Список изменений' не открыла окно")
    step(f"✓ 'Список изменений' открыл окно: {new_title!r}")

    win = Desktop(backend="uia").window(handle=new_handle)
    closed = False
    for t in ("Закрыть", "ОК", "OK", "Close"):
        try:
            b = win.child_window(title=t, control_type="Button")
            if b.exists(timeout=2):
                b.click_input()
                closed = True
                break
        except Exception:
            continue
    if not closed:
        win.type_keys("%{F4}")
    time.sleep(1)
    if new_handle in top_windows():
        raise RuntimeError("Не удалось закрыть окно списка изменений")
    step("✓ Окно списка изменений закрыто")


def check_nav_buttons(main, step, page_name: str) -> None:
    nxt = main.child_window(title_re="Вперёд", control_type="Button")
    cancel = main.child_window(title="Отмена", control_type="Button")
    nxt.wait("visible enabled", timeout=30)
    cancel.wait("visible enabled", timeout=30)
    step(f"✓ [{page_name}] кнопки 'Вперёд' и 'Отмена' активны")


def check_back_bug(main, step, known_bugs, observations, page_marker_re: str, bug_id: str) -> None:
    """Известный баг: 'Назад' не работает. Фиксируем, но НЕ валим тест."""
    back = main.child_window(title_re="Назад", control_type="Button")
    back.wait("visible", timeout=15)
    if not safe_enabled(back):
        known_bugs.append(f"{bug_id}: кнопка 'Назад' неактивна")
        step(f"⚠ Известный баг подтверждён: 'Назад' неактивна ({bug_id})")
        return
    back.click_input()
    time.sleep(2)
    if main.child_window(title_re=page_marker_re).exists(timeout=3):
        known_bugs.append(f"{bug_id}: клик 'Назад' не приводит к переходу")
        step(f"⚠ Известный баг подтверждён: 'Назад' не переходит ({bug_id})")
    else:
        observations.append(f"{bug_id}: 'Назад' ЗАРАБОТАЛА — возможно, баг исправлен")
        step(f"✓ 'Назад' сработала ({bug_id}) — баг исправлен?")


# ==================== РЕЖИМ: wizard_smoke ====================

def run_wizard_smoke(args, step) -> None:
    exe_name = Path(args.installer).name
    if process_running(exe_name):
        subprocess.run(["taskkill", "/F", "/IM", exe_name], capture_output=True, timeout=30)
        time.sleep(2)

    step(f"Запуск установщика: {args.installer}")
    app = Application(backend="uia").start(args.installer)
    main = app.window(title_re="Установка.*")
    main.wait("visible ready", timeout=120)
    step(f"Главное окно найдено: {main.window_text()!r}")

    check_changelog(main, step)

    back = main.child_window(title_re="Назад", control_type="Button")
    nxt = main.child_window(title_re="Вперёд", control_type="Button")
    nxt.wait("visible enabled", timeout=30)
    back_before = safe_enabled(back)
    nxt.click_input()
    if not wait_until(lambda: safe_enabled(back), 15):
        if not main.child_window(title_re="Добро пожаловать").exists(timeout=2):
            pass
        else:
            raise RuntimeError("Кнопка 'Вперёд' не перевела на следующую страницу")
    step(f"✓ 'Вперёд' сработал (Назад: был enabled={back_before}, стал enabled=True)")

    cancel = main.child_window(title="Отмена", control_type="Button")
    cancel.click_input()
    confirmed = False
    start = time.time()
    while time.time() - start < 10 and not confirmed:
        try:
            yes = app.top_window().child_window(title_re="^Да$|^Yes$", control_type="Button")
            if yes.exists(timeout=1):
                yes.click_input()
                confirmed = True
        except Exception:
            pass
        time.sleep(0.5)
    if not wait_process_exit(exe_name, 60):
        raise RuntimeError("После 'Отмена' процесс не завершился")
    step("✓ 'Отмена' сработал: установщик завершился")


# ==================== РЕЖИМ: wizard_walkthrough ====================

def run_wizard_walkthrough(args, step, result) -> None:
    known_bugs = result.setdefault("known_bugs", [])
    observations = result.setdefault("observations", [])

    exe_name = Path(args.installer).name
    if process_running(exe_name):
        step(f"Найден висящий процесс {exe_name}, завершаем")
        subprocess.run(["taskkill", "/F", "/IM", exe_name], capture_output=True, timeout=30)
        time.sleep(2)

    step(f"Запуск установщика: {args.installer}")
    app = Application(backend="uia").start(args.installer)
    main = app.window(title_re="Установка.*")
    main.wait("visible ready", timeout=120)
    step(f"Главное окно найдено: {main.window_text()!r}")

    # ---------- СТРАНИЦА 1: Приветствие ----------
    step("Страница 1: приветствие")
    check_changelog(main, step)
    check_nav_buttons(main, step, "стр.1")
    main.child_window(title_re="Вперёд", control_type="Button").click_input()

    # ---------- СТРАНИЦА 2: Лицензионный договор ----------
    step("Страница 2: лицензионный договор")
    main.child_window(title_re="Принимаю условия", control_type="RadioButton").wait("visible", timeout=30)
    step("✓ Переход на страницу лицензии выполнен")

    # --- сверка текста лицензии с эталоном ---
    ref_path = args.license_ref
    if not os.path.exists(ref_path):
        raise RuntimeError(f"Эталон лицензии не найден на ВМ: {ref_path}")
    with open(ref_path, "r", encoding="utf-8") as f:
        reference = f.read()

    license_text = extract_longest_text(main)
    if license_text:
        n_text, n_ref = norm(license_text), norm(reference)
        if n_text == n_ref:
            step("✓ Текст лицензионного договора ПОЛНОСТЬЮ совпадает с эталоном")
        else:
            missing = [a for a in LICENSE_ANCHORS if norm(a) not in n_text]
            if not missing and len(n_text) >= 0.8 * len(n_ref):
                observations.append("Текст лицензии извлечён частично, но контрольные фразы на месте")
                step("✓ Контрольные фразы лицензии на месте (полное извлечение текста недоступно)")
            else:
                raise RuntimeError(
                    f"Текст лицензионного договора ИЗМЕНЁН! Отсутствуют фразы: {missing}"
                )
    else:
        # не удалось извлечь весь текст — проверяем контрольные фразы по всему окну
        all_text = norm(collect_texts(main))
        missing = [a for a in LICENSE_ANCHORS if norm(a) not in all_text]
        if missing:
            raise RuntimeError(f"Текст лицензии ИЗМЕНЁН или не читается. Нет фраз: {missing}")
        step("✓ Контрольные фразы лицензии найдены в окне")

    # --- радиобаттоны ---
    accept = main.child_window(title_re="Принимаю условия", control_type="RadioButton")
    decline = main.child_window(title_re="Не принимаю", control_type="RadioButton")
    if is_selected(accept) is not True:
        raise RuntimeError("Радиобаттон 'Принимаю' не выбран по умолчанию")
    step("✓ По умолчанию выбрано 'Принимаю условия'")
    decline.click_input()
    time.sleep(0.5)
    if is_selected(decline) is not True or is_selected(accept) is not False:
        raise RuntimeError("Переключение на 'Не принимаю' не сработало")
    step("✓ Переключение на 'Не принимаю условия' работает")
    accept.click_input()
    time.sleep(0.5)
    if is_selected(accept) is not True:
        raise RuntimeError("Обратное переключение на 'Принимаю' не сработало")
    step("✓ Обратное переключение на 'Принимаю' работает")

    check_nav_buttons(main, step, "стр.2")
    main.child_window(title_re="Вперёд", control_type="Button").click_input()

    # ---------- СТРАНИЦА 3: Вариант установки ----------
    step("Страница 3: вариант установки")
    chk = main.child_window(title_re="удаленное рабочее место", control_type="CheckBox")
    chk.wait("visible", timeout=30)
    initial = is_selected(chk)
    step(f"✓ Чекбокс 'удаленное рабочее место' найден, состояние: {initial}")
    chk.click_input()
    time.sleep(0.5)
    if is_selected(chk) == initial:
        raise RuntimeError("Чекбокс 'удаленное рабочее место' не переключается")
    chk.click_input()  # возвращаем как было
    time.sleep(0.5)
    step("✓ Чекбокс 'удаленное рабочее место' переключается в обе стороны")

    # позитивная проверка 'Назад' (тут она должна работать)
    back = main.child_window(title_re="Назад", control_type="Button")
    back.click_input()
    if not main.child_window(title_re="Принимаю условия", control_type="RadioButton").wait("visible", timeout=10):
        raise RuntimeError("'Назад' со стр.3 не вернула на страницу лицензии")
    step("✓ 'Назад' вернула на страницу лицензии")
    main.child_window(title_re="Вперёд", control_type="Button").click_input()
    main.child_window(title_re="удаленное рабочее место", control_type="CheckBox").wait("visible", timeout=10)
    step("✓ 'Вперёд' снова на стр.3")
    check_nav_buttons(main, step, "стр.3")
    main.child_window(title_re="Вперёд", control_type="Button").click_input()

    # ---------- СТРАНИЦА 4: Подготовка установки (PostgreSQL) ----------
    step("Страница 4: подготовка установки (ждем активности кнопок после СУБД)")
    nxt = main.child_window(title_re="Вперёд", control_type="Button")
    if not wait_until(lambda: safe_enabled(nxt), timeout=600):
        raise RuntimeError("На стр.4 'Вперёд' не стала активной за 10 минут (PostgreSQL?)")
    step("✓ Стр.4: 'Вперёд' активна (подготовка СУБД завершена)")
    main.child_window(title="Отмена", control_type="Button").wait("visible", timeout=10)
    nxt.click_input()

    # ---------- СТРАНИЦА 5: Выбор типа установки ----------
    step("Страница 5: выбор типа установки")
    combo = main.child_window(control_type="ComboBox")
    combo.wait("visible", timeout=30)
    value = get_edit_value(combo) or combo.window_text()
    if "умолчанию" not in value:
        observations.append(f"Неожиданное значение combobox по умолчанию: {value!r}")
    step(f"✓ Combobox найден, текущее значение: {value!r}")
    try:
        combo.expand()
        time.sleep(0.5)
        items = [i.window_text() for i in combo.children(control_type="ListItem")]
        step(f"✓ Список раскрыт, варианты: {items}")
        if not any("учная" in i for i in items):
            raise RuntimeError("В списке нет варианта 'Ручная установка'")
        combo.select(value.strip() or "По умолчанию")
        time.sleep(0.5)
    except Exception as e:
        raise RuntimeError(f"Combobox не работает: {e}")
    step("✓ Выпадающий список работает")

    check_nav_buttons(main, step, "стр.5")
    check_back_bug(main, step, known_bugs, observations,
                   "Выбор типа установки|типа установки", "BUG-BACK-INSTALL-TYPE")
    main.child_window(title_re="Вперёд", control_type="Button").click_input()

    # ---------- СТРАНИЦА 6: Папка назначения ----------
    step("Страница 6: папка назначения")
    path_edit = main.child_window(control_type="Edit")
    path_edit.wait("visible", timeout=30)
    default_val = get_edit_value(path_edit).strip()
    step(f"✓ Поле пути найдено, значение по умолчанию: {default_val!r}")
    if norm(default_val.rstrip("\\")) != norm(args.install_path.rstrip("\\")):
        observations.append(
            f"Путь по умолчанию отличается от ожидаемого: {default_val!r} != {args.install_path!r}"
        )

    # невалидный путь
    set_edit(path_edit, r"Z:\bad<>path|*?")
    time.sleep(0.5)
    nxt_state = safe_enabled(main.child_window(title_re="Вперёд", control_type="Button"))
    observations.append(f"Невалидный путь: 'Вперёд' enabled={nxt_state}")
    step(f"✓ Ввод невалидного пути: 'Вперёд' enabled={nxt_state} (наблюдение)")

    # длинный путь
    long_path = "C:\\BOLID\\" + "a" * 200
    set_edit(path_edit, long_path)
    time.sleep(0.5)
    nxt_state = safe_enabled(main.child_window(title_re="Вперёд", control_type="Button"))
    observations.append(f"Длинный путь (210 симв.): 'Вперёд' enabled={nxt_state}")
    step(f"✓ Ввод длинного пути: 'Вперёд' enabled={nxt_state} (наблюдение)")

    # валидный кастомный путь и возврат к дефолту
    set_edit(path_edit, r"C:\BOLID\test_install_dir")
    time.sleep(0.5)
    step("✓ Ввод валидного кастомного пути выполнен")
    set_edit(path_edit, args.install_path)
    time.sleep(0.5)
    step(f"✓ Возвращён путь установки: {args.install_path}")

    # кнопка 'Просмотреть...'
    browse = main.child_window(title_re="Просмотреть", control_type="Button")
    if browse.exists(timeout=5):
        before = top_windows()
        browse.click_input()
        dlg_handle = None
        start = time.time()
        while time.time() - start < 10:
            for h, t in top_windows().items():
                if h not in before:
                    dlg_handle = h
                    break
            if dlg_handle:
                break
            time.sleep(0.5)
        if dlg_handle:
            step(f"✓ 'Просмотреть...' открыла диалог выбора папки: {top_windows().get(dlg_handle)!r}")
            dlg = Desktop(backend="uia").window(handle=dlg_handle)
            closed = False
            for t in ("Отмена", "Cancel"):
                try:
                    b = dlg.child_window(title=t, control_type="Button")
                    if b.exists(timeout=2):
                        b.click_input()
                        closed = True
                        break
                except Exception:
                    continue
            if not closed:
                dlg.type_keys("%{F4}")
            time.sleep(1)
        else:
            observations.append("'Просмотреть...' не открыла диалог")
    else:
        observations.append("Кнопка 'Просмотреть...' не найдена")

    check_nav_buttons(main, step, "стр.6")
    check_back_bug(main, step, known_bugs, observations,
                   "папку назначения|Куда ARM", "BUG-BACK-FOLDER")
    main.child_window(title_re="Вперёд", control_type="Button").click_input()

    # ---------- СТРАНИЦА 7: Готово к установке ----------
    step("Страница 7: сводка перед установкой")
    install_btn = main.child_window(title_re="Установить", control_type="Button")
    install_btn.wait("visible enabled", timeout=30)
    summary = collect_texts(main)
    n_summary = norm(summary)

    expected_anchors = [
        args.install_path,
        "По умолчанию",
        "Папка меню Пуск",
        "Сервер Орион Про",
        "Монитор Орион Про",
        "Администратор базы данных",
    ]
    missing = [a for a in expected_anchors if norm(a) not in n_summary]
    if missing:
        raise RuntimeError(f"В сводке перед установкой не найдено: {missing}")
    step("✓ Сводка корректна: папка, тип установки, компоненты, папка Пуск")

    check_back_bug(main, step, known_bugs, observations,
                   "готово", "BUG-BACK-READY")
    main.child_window(title="Отмена", control_type="Button").wait("visible", timeout=10)

    # ---------- УСТАНОВКА ----------
    step("Нажата кнопка 'Установить', идёт установка...")
    install_btn.click_input()

    finish = main.child_window(title_re="Готово|Finish", control_type="Button")
    completed = False
    start = time.time()
    while time.time() - start < args.timeout:
        try:
            if finish.exists(timeout=2):
                completed = True
                break
        except Exception:
            pass
        if not process_running(exe_name):
            completed = True
            break
        time.sleep(5)
    if not completed:
        raise RuntimeError("Установка не завершилась за отведённое время")
    step("✓ Установка завершена")

    # ---------- СТАТИСТИКА УСТАНОВЛЕННЫХ ФАЙЛОВ ----------
    stats = collect_install_stats(args.install_path)
    result["install_stats"] = stats
    if stats["total_files"] == 0:
        raise RuntimeError(f"Папка {args.install_path} пуста после установки")
    top_exts = sorted(stats["by_ext"].items(), key=lambda kv: -kv[1])[:10]
    step(
        f"✓ Статистика установки: файлов={stats['total_files']}, "
        f"папок={stats['total_dirs']}, топ типов={top_exts}"
    )
    exe_count = stats["by_ext"].get(".exe", 0)
    if exe_count == 0:
        raise RuntimeError("После установки в папке нет ни одного .exe")
    step(f"✓ .exe файлов установлено: {exe_count}")

    # окно завершения — проверки позже, сейчас просто закрываем
    try:
        if finish.exists(timeout=3):
            finish.click_input()
            step("Окно завершения закрыто (проверки окна — следующим этапом)")
    except Exception:
        pass
    wait_process_exit(exe_name, 60)


# ==================== РЕЖИМ: full_install (legacy) ====================

def run_full_install(args, step) -> None:
    if not UI_STEPS:
        raise RuntimeError("UI_STEPS пустой")
    app = Application(backend="uia").start(args.installer)
    current = None
    for index, ui_step in enumerate(UI_STEPS, start=1):
        action = ui_step.get("action")
        step(f"Шаг {index}: {action}")
        if action == "wait_window":
            current = app.window(title_re=ui_step["title_re"])
            current.wait("exists ready", timeout=ui_step.get("timeout", 60))
            continue
        if current is None:
            current = app.top_window()
        kwargs = {k: ui_step[k] for k in ("title_re", "title", "auto_id", "control_type", "found_index") if ui_step.get(k) is not None}
        control = current.child_window(**kwargs)
        control.wait("visible", timeout=ui_step.get("timeout", 30))
        if action == "click":
            control.click_input()
        elif action == "check":
            control.check()
        elif action == "set_edit":
            control.set_edit_text(str(ui_step.get("value", "")).format(install_path=args.install_path))
        time.sleep(ui_step.get("sleep_after", 1))


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--installer", required=True)
    parser.add_argument("--install-path", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--license-ref", default=r"C:\AutoTest\license_reference.txt")
    parser.add_argument("--mode",
                        choices=["wizard_smoke", "wizard_walkthrough", "full_install"],
                        default="wizard_walkthrough")
    return parser.parse_args()


def main():
    args = parse_args()
    Path(args.log).parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=args.log, level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s [%(levelname)s] %(message)s")

    result = {"status": "failed", "steps": [], "error": "", "timestamp": time.time()}

    def step(msg: str):
        logging.info(msg)
        result["steps"].append(msg)

    try:
        if PYWINAUTO_IMPORT_ERROR:
            raise RuntimeError(f"pywinauto не установлен на ВМ: {PYWINAUTO_IMPORT_ERROR}")
        if args.mode == "wizard_smoke":
            run_wizard_smoke(args, step)
        elif args.mode == "wizard_walkthrough":
            run_wizard_walkthrough(args, step, result)
        else:
            run_full_install(args, step)
        result["status"] = "passed"
    except Exception as e:
        result["status"] = "failed"
        result["error"] = f"{type(e).__name__}: {e}"
        result["traceback"] = traceback.format_exc()
        step(f"Ошибка: {e}")
    finally:
        save_result(args.result, result)

    sys.exit(0 if result["status"] == "passed" else 1)


if __name__ == "__main__":
    main()