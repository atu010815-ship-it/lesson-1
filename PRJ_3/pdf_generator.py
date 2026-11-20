#!/usr/bin/env python3
"""Генератор PDF-чеков из CSV/JSON данных и HTML-шаблонов."""

from __future__ import annotations

import csv
import json
import platform
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Пути
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent          # PRJ_3
PROJECT_DIR = SCRIPT_DIR.parent                       # Курс
DATA_DIR = PROJECT_DIR / "data"
TEMPLATES_DIR = PROJECT_DIR / "templates"
OUTPUT_DIR = PROJECT_DIR / "output"

# ---------------------------------------------------------------------------
# Утилиты
# ---------------------------------------------------------------------------


def ensure_directories() -> None:
    """Создаёт рабочие директории, если они отсутствуют."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def print_menu(title: str, options: list[str]) -> None:
    """Выводит нумерованное меню."""
    print(f"\n{title}")
    print("-" * len(title))
    if not options:
        print("  Нет доступных вариантов.")
        return
    for number, option in enumerate(options, start=1):
        print(f"  {number}. {option}")


def choose_from_menu(title: str, options: list[str], hint: str = "") -> int:
    """Запрашивает у пользователя номер варианта и возвращает индекс."""
    print_menu(title, options)
    prompt = f"\nВыберите номер {hint} (1-{len(options)}): " if hint else (
        f"\nВыберите номер (1-{len(options)}): "
    )
    while True:
        answer = input(prompt).strip()
        if answer.isdigit():
            number = int(answer)
            if 1 <= number <= len(options):
                return number - 1
        print(f"Некорректный выбор. Введите число от 1 до {len(options)}.")


# ---------------------------------------------------------------------------
# Чтение файлов
# ---------------------------------------------------------------------------


def read_csv(path: Path) -> list[dict[str, Any]]:
    """Парсит CSV (pandas → fallback csv.DictReader)."""
    # Пытаемся pandas
    try:
        import pandas as pd

        frame = pd.read_csv(path, encoding="utf-8-sig")
        return frame.where(frame.notna(), None).to_dict(orient="records")
    except UnicodeDecodeError:
        import pandas as pd

        frame = pd.read_csv(path, encoding="cp1251")
        return frame.where(frame.notna(), None).to_dict(orient="records")
    except ImportError:
        pass

    # Fallback — встроенный csv
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            with path.open("r", encoding=encoding, newline="") as handle:
                return list(csv.DictReader(handle))
        except UnicodeDecodeError:
            continue

    raise ValueError("Не удалось прочитать CSV-файл")


def normalize_records(payload: Any) -> list[dict[str, Any]]:
    """Приводит JSON-структуру к списку словарей."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if not isinstance(payload, dict):
        raise ValueError("JSON должен содержать объект или массив объектов")

    # Ищем первое вложенное поле со списком словарей
    for key, value in payload.items():
        if isinstance(value, list) and value and all(
            isinstance(item, dict) for item in value
        ):
            return value

    # Если значения — сами словари, превращаем в список
    if payload and all(isinstance(v, dict) for v in payload.values()):
        return list(payload.values())

    return [payload]


def read_json(path: Path) -> list[dict[str, Any]]:
    """Парсит JSON."""
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            with path.open("r", encoding=encoding) as handle:
                return normalize_records(json.load(handle))
        except UnicodeDecodeError:
            continue
    raise ValueError("Не удалось прочитать JSON-файл")


def read_data_file(path: Path) -> list[dict[str, Any]]:
    """Определяет тип файла и читает данные."""
    suffix = path.suffix.lower()
    if suffix in {".csv", ".cvs"}:
        return read_csv(path)
    if suffix == ".json":
        return read_json(path)
    raise ValueError(f"Неподдерживаемый тип файла: {path.suffix}")


# ---------------------------------------------------------------------------
# Поиск invoice id
# ---------------------------------------------------------------------------


def normalize_name(value: Any) -> str:
    """Убирает все символы кроме букв и цифр (кириллица + латиница)."""
    return re.sub(r"[^a-z0-9а-яё]", "", str(value).lower())


def find_invoice_key(record: dict[str, Any]) -> str | None:
    """Возвращает оригинальное имя ключа, содержащего invoice id."""
    priority = (
        "invoiceid",
        "invoice_id",
        "invoice",
        "checkid",
        "check_id",
        "billid",
        "bill_id",
        "id",
    )

    normalized = {normalize_name(key): key for key in record}
    for candidate in priority:
        if candidate in normalized:
            return normalized[candidate]

    for norm_key, orig_key in normalized.items():
        if "invoice" in norm_key or "check" in norm_key or "чек" in norm_key:
            return orig_key

    return None


def get_invoice_ids(
    records: list[dict[str, Any]],
) -> tuple[str | None, list[str]]:
    """Возвращает (имя_ключа, список уникальных id)."""
    key: str | None = None
    values: list[str] = []

    for record in records:
        if key is None:
            key = find_invoice_key(record)
        if key is not None:
            value = record.get(key)
            if value is not None:
                text = str(value).strip()
                if text and text not in values:
                    values.append(text)

    return key, values


# ---------------------------------------------------------------------------
# Рендер шаблона
# ---------------------------------------------------------------------------


def render_template(template_text: str, record: dict[str, Any]) -> str:
    """Заменяет {{ key }} в HTML-шаблоне на значения из record."""
    # Jinja2 — если доступна
    try:
        from jinja2 import Environment, StrictUndefined, UndefinedError

        env = Environment(
            autoescape=False,
            undefined=StrictUndefined,
            keep_trailing_newline=True,
        )
        try:
            return env.from_string(template_text).render(**record)
        except UndefinedError as error:
            print(f"  Предупреждение: {error}")
            env = Environment(autoescape=False, keep_trailing_newline=True)
            return env.from_string(template_text).render(**record)
    except ImportError:
        pass

    # Простая замена {{ key }}
    result = template_text
    for key, value in record.items():
        replacement = "" if value is None else str(value)
        pattern = r"\{\{\s*" + re.escape(str(key)) + r"\s*\}\}"
        result = re.sub(pattern, replacement, result)
    return result


# ---------------------------------------------------------------------------
# Шрифт и CSS для кириллицы
# ---------------------------------------------------------------------------


def find_font() -> Path | None:
    """Ищет DejaVu Sans или Arial Unicode на системе."""
    candidates = [
        Path("C:/Windows/Fonts/dejavusans.ttf"),
        Path("C:/Windows/Fonts/arialuni.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        Path("/Library/Fonts/Arial Unicode.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def font_css() -> str:
    """Генерирует @font-face CSS-блок для кириллицы."""
    font = find_font()
    if font is not None:
        font_uri = font.resolve().as_uri()
        return (
            "@font-face {"
            f' font-family: "DejaVu Sans"; src: url("{font_uri}") format("truetype");'
            "}"
            " body { font-family: 'DejaVu Sans', 'Roboto', sans-serif; }"
        )
    # Fallback — локальный шрифт
    return (
        "@font-face {"
        ' font-family: "DejaVu Sans"; src: local("DejaVu Sans");'
        "}"
        " body { font-family: 'DejaVu Sans', 'Roboto', sans-serif; }"
    )


# ---------------------------------------------------------------------------
# Генерация и открытие PDF
# ---------------------------------------------------------------------------


def open_pdf(path: Path) -> None:
    """Открывает PDF в системной программе просмотра."""
    system = platform.system()
    try:
        if system == "Windows":
            os_startfile(str(path))
        elif system == "Darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
        print(f"  PDF открыт в программе просмотра: {path}")
    except Exception as error:
        print(f"  Не удалось открыть PDF автоматически: {error}")
        print(f"  Путь к файлу: {path}")


def os_startfile(path: str) -> None:
    """Обёртка os.startfile для кросс-платформенности."""
    import os

    os.startfile(path)  # type: ignore[attr-defined]


def generate_pdf(html: str, output_path: Path) -> None:
    """Генерирует PDF через WeasyPrint."""
    try:
        from weasyprint import HTML

        HTML(string=html).write_pdf(str(output_path))
    except ImportError:
        print(
            "\n❌ Библиотека WeasyPrint не установлена.\n"
            "  Установите: pip install weasyprint"
        )
        sys.exit(1)
    except PermissionError as error:
        print(f"\n❌ Нет доступа для записи файла: {output_path}")
        print(f"   Причина: {error}")
        print(
            "   Скорее всего PDF уже открыт в программе просмотра и файл занят.\n"
            "   Закройте этот PDF и запустите скрипт снова."
        )
        sys.exit(1)
    except FileNotFoundError as error:
        print(f"\n❌ Не найден путь для сохранения PDF: {output_path}")
        print(f"   Причина: {error}")
        sys.exit(1)
    except OSError as error:
        print(f"\n❌ Не удалось запустить WeasyPrint: {error}")
        system = platform.system()
        if system == "Windows":
            print(
                "  На Windows установите GTK3 Runtime:\n"
                "  https://github.com/tschoonj/GTK-for-Windows-Runtime-Environment"
            )
        elif system == "Darwin":
            print("  На macOS: brew install pango glib")
        else:
            print("  На Linux: sudo apt install libpango-1.0-0")
        sys.exit(1)
    except OSError as error:
        print(f"\n\u274c Не удалось запустить WeasyPrint: {error}")
        system = platform.system()
        if system == "Windows":
            print(
                "  На Windows установите GTK3 Runtime:\n"
                "  https://github.com/tschoonj/GTK-for-Windows-Runtime-Environment"
            )
        elif system == "Darwin":
            print("  На macOS: brew install pango glib")
        else:
            print("  На Linux: sudo apt install libpango-1.0-0")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Основной поток
# ---------------------------------------------------------------------------


def main() -> None:
    ensure_directories()

    # Собираем файлы
    data_files = sorted(
        p
        for p in DATA_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in {".csv", ".cvs", ".json"}
    )
    templates = sorted(
        p
        for p in TEMPLATES_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in {".html", ".htm"}
    )

    print("=" * 40)
    print("  Генератор PDF-чеков")
    print("=" * 40)

    print_menu(
        "Доступные файлы с данными",
        [p.name for p in data_files],
    )
    print_menu(
        "Доступные HTML-шаблоны",
        [p.name for p in templates],
    )

    if not data_files:
        print(f"\n❌ Нет данных в директории: {DATA_DIR}")
        sys.exit(1)
    if not templates:
        print(f"\n❌ Нет шаблонов в директории: {TEMPLATES_DIR}")
        sys.exit(1)

    # Выбор файла данных и шаблона
    data_index = choose_from_menu(
        "Выберите файл данных",
        [p.name for p in data_files],
        hint="файла данных",
    )
    template_index = choose_from_menu(
        "Выберите HTML-шаблон",
        [p.name for p in templates],
        hint="HTML-шаблона",
    )

    data_path = data_files[data_index]
    template_path = templates[template_index]

    print(f"\n  Файл данных: {data_path.name}")
    print(f"  Шаблон:      {template_path.name}")

    # Чтение данных
    try:
        records = read_data_file(data_path)
    except Exception as error:
        print(f"\n❌ Ошибка чтения файла данных: {error}")
        sys.exit(1)

    if not records:
        print("\n❌ Выбранный файл не содержит записей.")
        sys.exit(1)

    # Список invoice id
    invoice_key, ids = get_invoice_ids(records)
    if invoice_key is None:
        print(
            "\n❌ В файле не найдено поле invoice id.\n"
            "  Поддерживаемые имена: invoice_id, invoice, check_id, id"
        )
        sys.exit(1)

    if not ids:
        print(f"\n❌ Поле «{invoice_key}» не содержит значений.")
        sys.exit(1)

    invoice_index = choose_from_menu(
        "Доступные invoice id",
        ids,
        hint="invoice id",
    )
    selected_id = ids[invoice_index]

    # Находим запись по id
    record = next(
        (r for r in records if str(r.get(invoice_key, "")).strip() == selected_id),
        None,
    )
    if record is None:
        print(f"\n❌ Не удалось найти запись с id: {selected_id}")
        sys.exit(1)

    # Чтение шаблона
    try:
        template_text = template_path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        template_text = template_path.read_text(encoding="cp1251")

    # Подстановка данных
    html = render_template(template_text, record)

    # Добавляем CSS для шрифта
    style_block = f"<style>{font_css()}</style>"
    if "<head>" in html.lower():
        html = re.sub(r"<head>", f"<head>{style_block}", html, count=1, flags=re.I)
    else:
        html = (
            "<html><head><meta charset=\"UTF-8\">"
            f"{style_block}"
            "</head><body>{html}</body></html>"
        )

    # Сохранение PDF
    safe_id = re.sub(r"[^\w.-]+", "_", selected_id, flags=re.UNICODE).strip("._")
    if not safe_id:
        safe_id = "invoice"

    output_path = OUTPUT_DIR / f"{safe_id}.pdf"
    generate_pdf(html, output_path)

    print(f"\n✅ PDF сохранён: {output_path}")
    open_pdf(output_path)


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\n\nРабота программы прервана.")
