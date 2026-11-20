from contextlib import contextmanager
from collections.abc import Generator
import calendar
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path


STATUSES = ("Ожидает", "Готово", "Просрочено", "Отменено")
ACTIVE_STATUSES = ("Ожидает", "Просрочено")
RECURRENCE_TYPES = ("Не повторять", "Ежедневно", "Еженедельно", "Ежемесячно", "Ежегодно")


@dataclass(frozen=True)
class Reminder:
    id: int
    title: str
    description: str
    due_at: str
    status: str
    recurrence: str = "Не повторять"


class ReminderStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path else Path(__file__).with_name("reminders.db")
        self.initialize()

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    due_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'Ожидает'
                        CHECK (status IN ('Ожидает', 'Готово', 'Просрочено', 'Отменено')),
                    notified_at TEXT,
                    recurrence TEXT NOT NULL DEFAULT 'Не повторять'
                        CHECK (recurrence IN ('Не повторять', 'Ежедневно', 'Еженедельно', 'Ежемесячно', 'Ежегодно'))
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_reminders_due_status "
                "ON reminders (status, due_at)"
            )
            # Миграция: добавить колонку recurrence, если её ещё нет
            try:
                connection.execute("ALTER TABLE reminders ADD COLUMN recurrence TEXT NOT NULL DEFAULT 'Не повторять'")
            except sqlite3.OperationalError:
                pass  # колонка уже существует

    @staticmethod
    def _to_reminder(row: sqlite3.Row) -> Reminder:
        return Reminder(
            id=row["id"],
            title=row["title"],
            description=row["description"],
            due_at=row["due_at"],
            status=row["status"],
            recurrence=row["recurrence"] if "recurrence" in row.keys() else "Не повторять",
        )

    def add_reminder(
        self, title: str, description: str, due_at: datetime, recurrence: str = "Не повторять"
    ) -> int:
        normalized_title = title.strip()
        if not normalized_title:
            raise ValueError("Заголовок не должен быть пустым.")
        if due_at.tzinfo is not None:
            raise ValueError("Укажите местное время без часового пояса.")
        if recurrence not in RECURRENCE_TYPES:
            raise ValueError(f"Неизвестный тип повтора: {recurrence}")

        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO reminders (title, description, due_at, recurrence) VALUES (?, ?, ?, ?)",
                (
                    normalized_title,
                    description.strip(),
                    due_at.isoformat(timespec="seconds"),
                    recurrence,
                ),
            )
            return int(cursor.lastrowid)

    def list_reminders(self, status: str | None = None) -> list[Reminder]:
        with self._connect() as connection:
            if status is None or status == "Все":
                rows = connection.execute(
                    "SELECT id, title, description, due_at, status, recurrence FROM reminders "
                    "ORDER BY due_at, id"
                ).fetchall()
            else:
                if status not in STATUSES:
                    raise ValueError(f"Неизвестный статус: {status}")
                rows = connection.execute(
                    "SELECT id, title, description, due_at, status, recurrence FROM reminders "
                    "WHERE status = ? ORDER BY due_at, id",
                    (status,),
                ).fetchall()
        return [self._to_reminder(row) for row in rows]

    def set_status(self, reminder_id: int, status: str) -> bool:
        if status not in ("Готово", "Отменено"):
            raise ValueError("Можно установить только статус «Готово» или «Отменено».")
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE reminders SET status = ? "
                "WHERE id = ? AND status IN (?, ?)",
                (status, reminder_id, *ACTIVE_STATUSES),
            )
            return cursor.rowcount > 0

    def delete_reminder(self, reminder_id: int) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
            return cursor.rowcount > 0

    def update_recurrence(self, reminder_id: int, recurrence: str) -> bool:
        if recurrence not in RECURRENCE_TYPES:
            raise ValueError(f"Неизвестный тип повтора: {recurrence}")
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE reminders SET recurrence = ? WHERE id = ?",
                (recurrence, reminder_id),
            )
            return cursor.rowcount > 0

    @staticmethod
    def _next_due_at(due_at: datetime, recurrence: str) -> datetime | None:
        """Возвращает следующую дату срабатывания для повторяющегося напоминания.

        Время нормализуется до целой минуты, чтобы сдвиг на секунды (например, при
        проверке просрочки) не «уплывал» во все повторения.
        """
        normalized = due_at.replace(second=0, microsecond=0)
        if recurrence == "Ежедневно":
            return normalized + timedelta(days=1)
        if recurrence == "Еженедельно":
            return normalized + timedelta(weeks=1)
        if recurrence == "Ежемесячно":
            month = normalized.month + 1
            year = normalized.year
            if month > 12:
                month = 1
                year += 1
            max_day = calendar.monthrange(year, month)[1]
            day = min(normalized.day, max_day)
            return normalized.replace(year=year, month=month, day=day)
        if recurrence == "Ежегодно":
            try:
                return normalized.replace(year=normalized.year + 1)
            except ValueError:
                # 29 февраля в невисокосном году -> 28 февраля
                return normalized.replace(year=normalized.year + 1, day=28)
        return None

    def process_due_reminders(self, now: datetime | None = None) -> list[Reminder]:
        current_time = now or datetime.now()
        if current_time.tzinfo is not None:
            raise ValueError("Ожидается местное время без часового пояса.")
        timestamp = current_time.isoformat(timespec="seconds")

        with self._connect() as connection:
            connection.execute(
                "UPDATE reminders SET status = 'Просрочено' "
                "WHERE status = 'Ожидает' AND due_at <= ?",
                (timestamp,),
            )
            rows = connection.execute(
                "SELECT id, title, description, due_at, status, recurrence FROM reminders "
                "WHERE status = 'Просрочено' AND due_at <= ? AND notified_at IS NULL "
                "ORDER BY due_at, id",
                (timestamp,),
            ).fetchall()
            if rows:
                connection.executemany(
                    "UPDATE reminders SET notified_at = ? WHERE id = ?",
                    [(timestamp, row["id"]) for row in rows],
                )
                # Для повторяющихся напоминаний создаём следующее срабатывание
                for row in rows:
                    recurrence = row["recurrence"] if "recurrence" in row.keys() else "Не повторять"
                    if recurrence != "Не повторять":
                        due_at_dt = datetime.fromisoformat(row["due_at"])
                        next_due = self._next_due_at(due_at_dt, recurrence)
                        if next_due:
                            connection.execute(
                                "INSERT INTO reminders (title, description, due_at, recurrence) "
                                "VALUES (?, ?, ?, ?)",
                                (
                                    row["title"],
                                    row["description"],
                                    next_due.isoformat(timespec="seconds"),
                                    recurrence,
                                ),
                            )
        return [self._to_reminder(row) for row in rows]
