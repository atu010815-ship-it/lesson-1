#!/usr/bin/env python3
"""
CLI-приложение для хранения паролей с шифрованием.

Использует:
  - SQLite3 для хранения данных
  - SHA-256 для хеширования мастер-пароля
  - Fernet (из cryptography) для шифрования паролей
"""

import sys
import os
import sqlite3
import hashlib
import getpass
import secrets
import string
import argparse

# ---------------------------------------------------------------------------
# Пути к файлам
# ---------------------------------------------------------------------------
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "passwords.db")
KEY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".key")

# ---------------------------------------------------------------------------
# Функции шифрования
# ---------------------------------------------------------------------------

def generate_fernet_key() -> bytes:
    """Генерирует новый Fernet-ключ."""
    from cryptography.fernet import Fernet
    return Fernet.generate_key()


def save_fernet_key(key: bytes) -> None:
    """Сохраняет Fernet-ключ в файл .key."""
    with open(KEY_PATH, "wb") as f:
        f.write(key)
    os.chmod(KEY_PATH, 0o600)  # только владелец может читать/писать


def load_fernet_key() -> bytes:
    """Загружает Fernet-ключ из файла .key."""
    with open(KEY_PATH, "rb") as f:
        return f.read()


def encrypt_password(plain: str, key: bytes) -> str:
    """Шифрует пароль и возвращает строку (base64)."""
    from cryptography.fernet import Fernet
    f = Fernet(key)
    return f.encrypt(plain.encode("utf-8")).decode("utf-8")


def decrypt_password(cipher: str, key: bytes) -> str:
    """Дешифрует пароль из base64-строки."""
    from cryptography.fernet import Fernet
    f = Fernet(key)
    return f.decrypt(cipher.encode("utf-8")).decode("utf-8")

# ---------------------------------------------------------------------------
# Функции БД
# ---------------------------------------------------------------------------

def get_db_connection() -> sqlite3.Connection:
    """Возвращает соединение с SQLite-базой, создавая таблицу если нужно."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS credentials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            login TEXT NOT NULL,
            encrypted_password TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS master_hash (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            password_hash TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


def init_master_password(master_pass: str) -> None:
    """Сохраняет хеш мастер-пароля в БД."""
    h = hashlib.sha256(master_pass.encode("utf-8")).hexdigest()
    conn = get_db_connection()
    conn.execute("INSERT OR REPLACE INTO master_hash (id, password_hash) VALUES (1, ?)", (h,))
    conn.commit()
    conn.close()


def verify_master_password(master_pass: str) -> bool:
    """Проверяет мастер-пароль по сохранённому хешу."""
    h = hashlib.sha256(master_pass.encode("utf-8")).hexdigest()
    conn = get_db_connection()
    row = conn.execute("SELECT password_hash FROM master_hash WHERE id = 1").fetchone()
    conn.close()
    if row is None:
        return False
    return secrets.compare_digest(row[0], h)


def add_record(name: str, login: str, password: str, key: bytes) -> None:
    """Добавляет новую запись в БД."""
    encrypted = encrypt_password(password, key)
    conn = get_db_connection()
    try:
        conn.execute(
            "INSERT INTO credentials (name, login, encrypted_password) VALUES (?, ?, ?)",
            (name, login, encrypted),
        )
        conn.commit()
        print(f"Запись «{name}» успешно добавлена.")
    except sqlite3.IntegrityError:
        print(f"Ошибка: запись с названием «{name}» уже существует.")
    finally:
        conn.close()


def get_record(name: str, key: bytes) -> None:
    """Выводит логин и расшифрованный пароль по названию."""
    conn = get_db_connection()
    row = conn.execute(
        "SELECT login, encrypted_password FROM credentials WHERE name = ?", (name,)
    ).fetchone()
    conn.close()

    if row is None:
        print(f"Запись «{name}» не найдена.")
        return

    login, enc_pass = row
    try:
        password = decrypt_password(enc_pass, key)
    except Exception:
        print("Ошибка: не удалось расшифровать пароль (неверный ключ).")
        return

    print(f"Название : {name}")
    print(f"Логин    : {login}")
    print(f"Пароль   : {password}")


def list_records() -> None:
    """Выводит список всех записей (название и логин)."""
    conn = get_db_connection()
    rows = conn.execute("SELECT name, login FROM credentials ORDER BY name").fetchall()
    conn.close()

    if not rows:
        print("Нет сохранённых записей.")
        return

    print(f"{'Название':<25} {'Логин'}")
    print("-" * 50)
    for name, login in rows:
        print(f"{name:<25} {login}")


def delete_record(name: str) -> None:
    """Удаляет запись по названию."""
    conn = get_db_connection()
    cursor = conn.execute("DELETE FROM credentials WHERE name = ?", (name,))
    conn.commit()
    conn.close()

    if cursor.rowcount == 0:
        print(f"Запись «{name}» не найдена.")
    else:
        print(f"Запись «{name}» удалена.")

# ---------------------------------------------------------------------------
# Утилиты
# ---------------------------------------------------------------------------

def generate_password(length: int = 16) -> str:
    """Генерирует случайный надёжный пароль."""
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    while True:
        password = "".join(secrets.choice(alphabet) for _ in range(length))
        # минимум: 1 строчная, 1 заглавная, 1 цифра, 1 спецсимвол
        if (
            any(c.islower() for c in password)
            and any(c.isupper() for c in password)
            and any(c.isdigit() for c in password)
            and any(c in "!@#$%^&*" for c in password)
        ):
            return password


def hidden_prompt(text: str) -> str:
    """Запрашивает пароль с скрытым вводом (getpass с fallback)."""
    try:
        return getpass.getpass(text)
    except (EOFError, KeyboardInterrupt):
        # Fallback для окружений без TTY
        return input(text)


def prompt_master_password() -> str:
    """Запрашивает мастер-пароль у пользователя (ввод скрыт)."""
    return hidden_prompt("Введите мастер-пароль: ")

# ---------------------------------------------------------------------------
# Основной обработчик CLI
# ---------------------------------------------------------------------------

def cmd_init(args: argparse.Namespace) -> None:
    """Первичная инициализация: создание мастер-пароля и ключа."""
    if os.path.exists(KEY_PATH):
        print("Ошибка: мастер-пароль уже установлен. Используйте команду «new» для смены.")
        return

    while True:
        pass1 = hidden_prompt("Создайте мастер-пароль: ")
        pass2 = hidden_prompt("Повторите мастер-пароль: ")
        if pass1 == pass2:
            break
        print("Пароли не совпадают. Попробуйте снова.")

    init_master_password(pass1)

    # Генерация и сохранение ключа шифрования
    key = generate_fernet_key()
    save_fernet_key(key)
    print("Мастер-пароль установлен. Готово к использованию.")


def cmd_change(args: argparse.Namespace) -> None:
    """Смена мастер-пароля."""
    # Проверяем старый пароль
    old = prompt_master_password()
    if not verify_master_password(old):
        print("Неверный мастер-пароль.")
        return

    while True:
        pass1 = hidden_prompt("Введите новый мастер-пароль: ")
        pass2 = hidden_prompt("Повторите новый мастер-пароль: ")
        if pass1 == pass2:
            break
        print("Пароли не совпадают. Попробуйте снова.")

    init_master_password(pass1)
    print("Мастер-пароль изменён.")


def cmd_add(args: argparse.Namespace) -> None:
    """Добавить запись."""
    key = load_fernet_key()
    name = input("Название (например, Google): ").strip()
    if not name:
        print("Название не может быть пустым.")
        return
    login = input("Логин: ").strip()
    if not login:
        print("Логин не может быть пустым.")
        return
    # Генерация пароля
    while True:
        try:
            length_str = input("Длина пароля (1-64, спецсимволы !@#$%^&*): ").strip()
            length = int(length_str)
            if 1 <= length <= 64:
                break
            print("Длина должна быть от 1 до 64.")
        except ValueError:
            print("Введите число.")

    password = generate_password(length)
    print(f"Сгенерированный пароль: {password}")

    add_record(name, login, password, key)


def cmd_get(args: argparse.Namespace) -> None:
    """Получить запись по названию."""
    key = load_fernet_key()
    name = input("Введите название записи: ").strip()
    get_record(name, key)


def cmd_list(args: argparse.Namespace) -> None:
    """Показать все записи."""
    list_records()


def cmd_delete(args: argparse.Namespace) -> None:
    """Удалить запись по названию."""
    name = input("Введите название записи для удаления: ").strip()
    delete_record(name)


def cmd_new(args: argparse.Namespace) -> None:
    """Создать новый мастер-пароль (пересоздать БД и ключ)."""
    if not os.path.exists(KEY_PATH):
        print("Мастер-пароль ещё не установлен. Используйте команду «init».")
        return

    confirm = input(
        "ВНИМАНИЕ! Это удалит ВСЕ сохранённые данные. Продолжить? (yes/no): "
    ).strip().lower()
    if confirm != "yes":
        print("Отменено.")
        return

    # Удаляем старые файлы
    os.remove(KEY_PATH)
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    # Повторяем инициализацию
    while True:
        pass1 = hidden_prompt("Создайте новый мастер-пароль: ")
        pass2 = hidden_prompt("Повторите новый мастер-пароль: ")
        if pass1 == pass2:
            break
        print("Пароли не совпадают. Попробуйте снова.")

    init_master_password(pass1)
    key = generate_fernet_key()
    save_fernet_key(key)
    print("Данные удалены, мастер-пароль создан заново.")


def cmd_generate(args: argparse.Namespace = None) -> None:
    """Сгенерировать случайный пароль."""
    length = 16
    if args is not None and hasattr(args, "length") and args.length:
        length = args.length
    pw = generate_password(length)
    print(f"Сгенерированный пароль ({length} символов):\n  {pw}")


def show_menu() -> None:
    """Показывает интерактивное меню и обрабатывает выбор пользователя."""
    print("\n=== Меню ===")
    print("1. Добавить запись       (add)")
    print("2. Получить пароль       (get)")
    print("3. Список записей        (list)")
    print("4. Удалить запись        (delete)")
    print("5. Сгенерировать пароль  (generate)")
    print("6. Сменить мастер-пароль (change)")
    print("7. Выход                 (exit)")
    print("==============\n")

    choice = input("Выберите действие: ").strip().lower()

    # Команды без авторизации
    if choice in ("5", "generate"):
        cmd_generate(None)
        return

    # Команды с авторизацией
    if not os.path.exists(KEY_PATH):
        print("Ошибка: мастер-пароль не установлен. Запустите «python password_manager.py init».")
        return

    if not os.path.exists(DB_PATH):
        print("Ошибка: база данных не найдена. Запустите «python password_manager.py init».")
        return

    master = prompt_master_password()
    if not verify_master_password(master):
        print("Неверный мастер-пароль.")
        return

    commands = {
        "1": cmd_add,
        "2": cmd_get,
        "3": cmd_list,
        "4": cmd_delete,
        "add": cmd_add,
        "get": cmd_get,
        "list": cmd_list,
        "delete": cmd_delete,
        "change": cmd_change,
    }

    handler = commands.get(choice)
    if handler:
        handler(None)
    elif choice in ("6", "change"):
        cmd_change(None)
    elif choice in ("7", "exit"):
        print("До свидания!")
    else:
        print("Неверный выбор. Попробуйте снова.\n")
        show_menu()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Password Manager — CLI-хранилище паролей",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:
  python password_manager.py init          # первичная настройка
  python password_manager.py add           # добавить запись
  python password_manager.py get           # получить запись
  python password_manager.py list          # список записей
  python password_manager.py delete        # удалить запись
  python password_manager.py new           # сбросить все данные
  python password_manager.py generate      # сгенерировать пароль
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Доступные команды")

    # init
    subparsers.add_parser("init", help="Первичная инициализация (создать мастер-пароль)")

    # new — сброс всех данных
    subparsers.add_parser("new", help="Удалить все данные и создать новый мастер-пароль")

    # add
    subparsers.add_parser("add", help="Добавить новую запись")

    # get
    subparsers.add_parser("get", help="Получить пароль по названию")

    # list
    subparsers.add_parser("list", help="Показать список всех записей")

    # delete
    subparsers.add_parser("delete", help="Удалить запись по названию")

    # generate
    gen_parser = subparsers.add_parser("generate", help="Сгенерировать случайный пароль")
    gen_parser.add_argument(
        "-l", "--length", type=int, default=16, help="Длина пароля (по умолчанию 16)"
    )

    # change
    subparsers.add_parser("change", help="Сменить мастер-пароль")

    args = parser.parse_args()

    if args.command is None:
        show_menu()
        return

    # Команды, не требующие авторизации
    if args.command == "init":
        cmd_init(args)
        return

    if args.command == "generate":
        cmd_generate(args)
        return

    # Команда new — требует мастер-пароль (БД должна существовать)
    if args.command == "new":
        cmd_new(args)
        return

    # Проверка наличия ключа и БД
    if not os.path.exists(KEY_PATH):
        print("Ошибка: мастер-пароль не установлен. Запустите «python password_manager.py init».")
        return

    if not os.path.exists(DB_PATH):
        print("Ошибка: база данных не найдена. Запустите «python password_manager.py init».")
        return

    # Проверка мастер-пароля
    master = prompt_master_password()
    if not verify_master_password(master):
        print("Неверный мастер-пароль.")
        return

    # Диспетчер команд
    commands = {
        "add": cmd_add,
        "get": cmd_get,
        "list": cmd_list,
        "delete": cmd_delete,
        "change": cmd_change,
    }

    handler = commands.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
