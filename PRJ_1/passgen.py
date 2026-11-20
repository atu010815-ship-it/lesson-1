from __future__ import annotations

import argparse
from contextlib import contextmanager
import getpass
import hashlib
import hmac
import secrets
import sqlite3
import string
import sys
from collections.abc import Iterator
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "passwords.db"
KEY_PATH = APP_DIR / ".key"


@contextmanager
def connect_db() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize_db() -> None:
    with connect_db() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS auth (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service TEXT NOT NULL COLLATE NOCASE UNIQUE,
                login TEXT NOT NULL,
                password TEXT NOT NULL
            )
            """
        )


def get_cipher() -> Fernet:
    if not KEY_PATH.exists():
        with connect_db() as connection:
            has_entries = connection.execute(
                "SELECT EXISTS(SELECT 1 FROM entries)"
            ).fetchone()[0]
        if has_entries:
            raise RuntimeError(
                f"Файл ключа {KEY_PATH.name} отсутствует, но в базе есть записи. "
                "Создание нового ключа сделало бы их недоступными."
            )
        try:
            with KEY_PATH.open("xb") as key_file:
                key_file.write(Fernet.generate_key())
        except FileExistsError:
            pass

    try:
        return Fernet(KEY_PATH.read_bytes())
    except (OSError, ValueError) as error:
        raise RuntimeError(f"Не удалось прочитать ключ шифрования: {error}") from error


def encrypt_password(cipher: Fernet, password: str) -> str:
    return cipher.encrypt(password.encode("utf-8")).decode("ascii")


def decrypt_password(cipher: Fernet, encrypted_password: str) -> str:
    try:
        return cipher.decrypt(encrypted_password.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeError) as error:
        raise RuntimeError(
            "Не удалось расшифровать пароль. Проверьте файл .key."
        ) from error


def hash_master_password(password: str, salt: bytes) -> str:
    return hashlib.sha256(salt + password.encode("utf-8")).hexdigest()


def authenticate() -> None:
    with connect_db() as connection:
        saved = connection.execute(
            "SELECT password_hash, salt FROM auth WHERE id = 1"
        ).fetchone()

        if saved is None:
            has_entries = connection.execute(
                "SELECT EXISTS(SELECT 1 FROM entries)"
            ).fetchone()[0]
            if has_entries:
                raise RuntimeError(
                    "В базе уже есть записи, но мастер-пароль не найден. "
                    "Отказ от повторной регистрации."
                )
            while True:
                password = getpass.getpass("Создайте мастер-пароль: ")
                if not password:
                    print("Мастер-пароль не должен быть пустым.")
                    continue
                confirmation = getpass.getpass("Повторите мастер-пароль: ")
                if password != confirmation:
                    print("Пароли не совпадают. Попробуйте ещё раз.")
                    continue
                salt = secrets.token_bytes(16)
                connection.execute(
                    "INSERT INTO auth (id, password_hash, salt) VALUES (1, ?, ?)",
                    (hash_master_password(password, salt), salt.hex()),
                )
                print("Мастер-пароль сохранён.")
                return

    password = getpass.getpass("Мастер-пароль: ")
    salt = bytes.fromhex(saved["salt"])
    supplied_hash = hash_master_password(password, salt)
    if not hmac.compare_digest(supplied_hash, saved["password_hash"]):
        raise PermissionError("Неверный мастер-пароль.")


def add_entry(cipher: Fernet) -> None:
    service = input("Название/откуда: ").strip()
    login = input("Логин: ").strip()
    password = getpass.getpass("Пароль: ")
    if not service or not login:
        raise ValueError("Название и логин не должны быть пустыми.")
    if not password:
        raise ValueError("Пароль не должен быть пустым.")

    try:
        with connect_db() as connection:
            connection.execute(
                "INSERT INTO entries (service, login, password) VALUES (?, ?, ?)",
                (service, login, encrypt_password(cipher, password)),
            )
    except sqlite3.IntegrityError as error:
        raise ValueError(f"Запись «{service}» уже существует.") from error
    print(f"Запись «{service}» добавлена.")


def get_entry(cipher: Fernet, service: str) -> None:
    with connect_db() as connection:
        entry = connection.execute(
            "SELECT password FROM entries WHERE service = ?", (service,)
        ).fetchone()
    if entry is None:
        raise ValueError(f"Запись «{service}» не найдена.")
    print(decrypt_password(cipher, entry["password"]))


def list_entries() -> None:
    with connect_db() as connection:
        entries = connection.execute(
            "SELECT service, login FROM entries ORDER BY service COLLATE NOCASE"
        ).fetchall()
    if not entries:
        print("Сохранённых записей нет.")
        return
    print(f"{'Название':<30} Логин")
    for entry in entries:
        print(f"{entry['service']:<30} {entry['login']}")


def delete_entry(service: str) -> None:
    with connect_db() as connection:
        cursor = connection.execute(
            "DELETE FROM entries WHERE service = ?", (service,)
        )
    if cursor.rowcount == 0:
        raise ValueError(f"Запись «{service}» не найдена.")
    print(f"Запись «{service}» удалена.")


def generate_password(length: int) -> str:
    if length < 4:
        raise ValueError("Длина пароля должна быть не меньше 4.")
    groups = (
        string.ascii_lowercase,
        string.ascii_uppercase,
        string.digits,
        "!@#$%^&*()-_=+",
    )
    characters = [secrets.choice(group) for group in groups]
    alphabet = "".join(groups)
    characters.extend(secrets.choice(alphabet) for _ in range(length - len(characters)))
    secrets.SystemRandom().shuffle(characters)
    return "".join(characters)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Локальное хранилище паролей с шифрованием Fernet."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("add", help="добавить новую запись")
    get_parser = commands.add_parser("get", help="показать пароль по названию")
    get_parser.add_argument("service", nargs="?", help="название записи")
    commands.add_parser("list", help="показать названия и логины")
    delete_parser = commands.add_parser("delete", help="удалить запись по названию")
    delete_parser.add_argument("service", nargs="?", help="название записи")
    new_parser = commands.add_parser("new", help="сгенерировать новый пароль")
    new_parser.add_argument(
        "--length", type=int, default=20, help="длина пароля (по умолчанию: 20)"
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        initialize_db()
        cipher = get_cipher()
        authenticate()

        if args.command == "add":
            add_entry(cipher)
        elif args.command == "get":
            service = args.service or input("Название/откуда: ").strip()
            if not service:
                raise ValueError("Название не должно быть пустым.")
            get_entry(cipher, service)
        elif args.command == "list":
            list_entries()
        elif args.command == "delete":
            service = args.service or input("Название/откуда: ").strip()
            if not service:
                raise ValueError("Название не должно быть пустым.")
            delete_entry(service)
        elif args.command == "new":
            print(generate_password(args.length))
        return 0
    except (OSError, PermissionError, RuntimeError, sqlite3.Error, ValueError) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())