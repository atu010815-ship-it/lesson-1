from __future__ import annotations

import csv
import json
import os
import platform
import re
import subprocess
from pathlib import Path
from typing import Any, Iterable


SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "data"
TEMPLATES_DIR = SCRIPT_DIR / "templates"
OUTPUT_DIR = SCRIPT_DIR / "output"


def ensure_directories() -> None:
    """Create workspace directories needed by the script."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def read_csv(path: Path) -> list[dict[str, Any]]:
    """Read a CSV with pandas when available, with csv.DictReader as fallback."""
    try:
        import pandas as pd

        frame = pd.read_csv(path, encoding="utf-8-sig")
        return frame.where(frame.notna(), None).to_dict(orient="records")
    except UnicodeDecodeError:
        import pandas as pd

        frame = pd.read_csv(path, encoding="cp1251")
        return frame.where(frame.notna(), None).to_dict(orient="records")
    except ImportError:
        for encoding in ("utf-8-sig", "cp1251"):
            try:
                with path.open("r", encoding=encoding, newline="") as handle:
                    return list(csv.DictReader(handle))
            except UnicodeDecodeError:
                continue
    raise ValueError("Не удалось прочитать CSV-файл")


def normalize_records(payload: Any) -> list[dict[str, Any]]:
    """Convert common JSON shapes to a flat list of record dictionaries."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if not isinstance(payload, dict):
        raise ValueError("JSON должен содержать объект или массив объектов")

    if "items" in payload and isinstance(payload["items"], list):
        payload = payload["items"]
        return [item for item in payload if isinstance(item, dict)]

    for key, value in payload.items():
        if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
            return value

    if all(isinstance(value, dict) for value in payload.values()) and payload:
        records: list[dict[str, Any]] = []
        for key, value in payload.items():
            record = dict(value)
            if not find_invoice_key(record):
                record.setdefault("invoice_id", key)
            records.append(record)
        return records

    return [payload]


def read_json(path: Path) -> list[dict[str, Any]]:
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            with path.open("r", encoding=encoding) as handle:
                return normalize_records(json.load(handle))
        except UnicodeDecodeError:
            continue
    raise ValueError("Не удалось прочитать JSON-файл")


def read_data_file(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix in {".csv", ".cvs"}:
        return read_csv(path)
    if suffix == ".json":
        return read_json(path)
    raise ValueError(f"Неподдерживаемый тип файла: {path.suffix}")


def find_invoice_key(record: dict[str, Any]) -> str | None:
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

    normalized = {normalize_name(key): str(key) for key in record}
    for candidate in priority:
        if candidate in normalized:
            return normalized[candidate]

    for normalized_key, original_key in normalized.items():
        if "invoice" in normalized_key or "чек" in normalized_key:
            return original_key
    return None


def normalize_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9а-яё]", "", str(value).lower().replace(" ", "_"))


def invoice_ids(records: Iterable[dict[str, Any]]) -> tuple[str | None, list[str]]:
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


def print_menu(title: str, options: list[str]) -> None:
    print(f"\n{title}")
    print("-" * len(title))
    if not options:
        print("  Нет доступных вариантов.")
        return
    for number, option in enumerate(options, start=1):
        print(f"  {number}. {option}")


def choose_from_menu(title: str, options: list[str]) -> int:
    print_menu(title, options)
    while True:
        answer = input("\nВыберите номер: ").strip()
        if answer.isdigit():
            number = int(answer)
            if 1 <= number <= len(options):
                return number - 1
        print("Некорректный выбор. Введите номер из списка.")


def render_template(template_text: str, record: dict[str, Any]) -> str:
    try:
        from jinja2 import Environment, StrictUndefined, UndefinedError

        environment = Environment(
            autoescape=False,
            undefined=StrictUndefined,
            keep_trailing_newline=True,
        )
        try:
            return environment.from_string(template_text).render(
                **record,
                record=record,
                invoice=record,
                data=record,
            )
        except UndefinedError as error:
            print(f"Предупреждение: {error}. Остальные поля будут пустыми.")
            environment = Environment(autoescape=False, keep_trailing_newline=True)
            return environment.from_string(template_text).render(
                **record,
                record=record,
                invoice=record,
                data=record,
            )
    except ImportError:
        result = template_text
        for key, value in record.items():
            replacement = "" if value is None else str(value)
            result = re.sub(
                rf"\{{\{{\s*(?:record\.|invoice\.|data\.)?{re.escape(str(key))}\s*\}}\}}",
                lambda _match, text=replacement: text,
                result,
            )
        return result


def find_font() -> Path | None:
    candidates = (
        Path("C:/Windows/Fonts/dejavusans.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/Library/Fonts/DejaVu Sans.ttf"),
        Path("/Library/Fonts/Arial Unicode.ttf"),
        Path("/System/Library/Fonts/Supplemental/DejaVu Sans.ttf"),
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def font_css() -> str:
    font = find_font()
    if font is not None:
        font_uri = font.resolve().as_uri()
        return (
            "@font-face {"
            f' font-family: "DejaVu Sans"; src: url("{font_uri}") format("truetype");'
            "}"
            " body { font-family: 'DejaVu Sans', 'Roboto', sans-serif; }"
        )
    return (
        "@font-face {"
        ' font-family: "DejaVu Sans"; src: local("DejaVu Sans");'
        "}"
        " body { font-family: 'DejaVu Sans', 'Roboto', sans-serif; }"
    )


def open_pdf(path: Path) -> None:
    system = platform.system()
    try:
        if system == "Windows":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif system == "Darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError as error:
        print(f"Не удалось открыть PDF автоматически: {error}")
        print(f"Путь к PDF: {path}")


def main() -> None:
    ensure_directories()

    data_files = sorted(
        path
        for path in DATA_DIR.iterdir()
        if path.is_file() and path.suffix.lower() in {".csv", ".cvs", ".json"}
    )
    templates = sorted(
        path for path in TEMPLATES_DIR.iterdir()
        if path.is_file() and path.suffix.lower() in {".html", ".htm"}
    )

    print("Генератор PDF-чеков")
    print("===================")
    print_menu(
        "Доступные файлы с данными",
        [path.name for path in data_files],
    )
    print_menu(
        "Доступные HTML-шаблоны",
        [path.name for path in templates],
    )

    if not data_files:
        print(f"\nНет CSV или JSON файлов в директории: {DATA_DIR}")
        return
    if not templates:
        print(f"\nНет HTML-шаблонов в директории: {TEMPLATES_DIR}")
        return

    data_index = choose_from_menu("Выберите файл данных", [path.name for path in data_files])
    template_index = choose_from_menu("Выберите HTML-шаблон", [path.name for path in templates])
    data_path = data_files[data_index]
    template_path = templates[template_index]

    try:
        records = read_data_file(data_path)
    except (ValueError, csv.Error, json.JSONDecodeError, OSError) as error:
        print(f"\nОшибка чтения файла данных: {error}")
        return

    if not records:
        print("\nВыбранный файл не содержит записей.")
        return

    invoice_key, ids = invoice_ids(records)
    if invoice_key is None:
        print("\nВ файле не найдено поле invoice id.")
        print("Поддерживаемые названия: invoice_id, invoice id, invoice, id.")
        return
    if not ids:
        print(f"\nПоле «{invoice_key}» не содержит значений.")
        return

    invoice_index = choose_from_menu("Доступные invoice id", ids)
    selected_id = ids[invoice_index]
    record = next(
        (
            item
            for item in records
            if str(item.get(invoice_key, "")).strip() == selected_id
        ),
        None,
    )
    if record is None:
        print("\nНе удалось найти выбранный invoice id.")
        return

    try:
        template_text = template_path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        template_text = template_path.read_text(encoding="cp1251")

    html = render_template(template_text, record)
    if "<head>" in html.lower():
        style = f"<style>{font_css()}</style>"
        html = re.sub(r"<head>", f"<head>{style}", html, count=1, flags=re.I)
    else:
        html = f"<html><head><meta charset=\"UTF-8\"><style>{font_css()}</style></head><body>{html}</body></html>"

    safe_id = re.sub(r"[^\w.-]+", "_", selected_id, flags=re.UNICODE).strip("._")
    if not safe_id:
        safe_id = "invoice"
    output_path = OUTPUT_DIR / f"{safe_id}.pdf"

    try:
        from weasyprint import HTML

        HTML(string=html, base_url=str(template_path.parent)).write_pdf(str(output_path))
    except ImportError:
        print("\nБиблиотека WeasyPrint не установлена.")
        print("Установите зависимости командой:")
        print("  pip install -r requirements.txt")
        return
    except OSError as error:
        print(f"\nНе удалось запустить WeasyPrint: {error}")
        if platform.system() == "Windows":
            print(
                "На Windows установите GTK3 Runtime и перезапустите терминал."
            )
        elif platform.system() == "Darwin":
            print("На macOS установите библиотеку Pango через: brew install pango")
        else:
            print("Установите системные библиотеки Pango и GTK.")
        return
    except Exception as error:
        print(f"\nОшибка генерации PDF: {error}")
        return1

    print(f"\nPDF сохранён: {output_path}")
    open_pdf(output_path)


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nРабота программы прервана.")