import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from database import ReminderStore


class ReminderStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = ReminderStore(Path(self.temp_dir.name) / "test.sqlite3")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_due_reminder_becomes_overdue_and_is_returned_only_once(self) -> None:
        now = datetime(2026, 5, 1, 12, 0)
        reminder_id = self.store.add_reminder(
            "Позвонить", "Описание", now - timedelta(seconds=1)
        )

        due = self.store.process_due_reminders(now)

        self.assertEqual([item.id for item in due], [reminder_id])
        self.assertEqual(due[0].status, "Просрочено")
        self.assertEqual(self.store.list_reminders()[0].status, "Просрочено")
        self.assertEqual(self.store.process_due_reminders(now), [])

    def test_status_filter_delete_and_terminal_status(self) -> None:
        due_at = datetime(2026, 5, 2, 9, 30)
        first_id = self.store.add_reminder("Первое", "", due_at)
        second_id = self.store.add_reminder("Второе", "", due_at)

        self.assertTrue(self.store.set_status(first_id, "Готово"))
        self.assertEqual(
            [item.title for item in self.store.list_reminders("Готово")], ["Первое"]
        )
        self.assertFalse(self.store.set_status(first_id, "Отменено"))
        self.assertTrue(self.store.delete_reminder(second_id))
        self.assertEqual([item.id for item in self.store.list_reminders()], [first_id])

    def test_rejects_empty_title_and_unknown_filter(self) -> None:
        with self.assertRaises(ValueError):
            self.store.add_reminder("  ", "", datetime(2026, 5, 2, 9, 30))
        with self.assertRaises(ValueError):
            self.store.list_reminders("Неизвестен")

    def test_recurring_reminder_creates_next_occurrence(self) -> None:
        """После просрочки повторяющегося напоминания создаётся следующее."""
        now = datetime(2026, 5, 1, 12, 0)
        reminder_id = self.store.add_reminder(
            "Пробежка", "Описание", now - timedelta(minutes=1), "Ежедневно"
        )

        due = self.store.process_due_reminders(now)

        self.assertEqual([item.id for item in due], [reminder_id])
        self.assertEqual(due[0].recurrence, "Ежедневно")

        # Следующее вхождение должно появиться в списке со статусом "Ожидает"
        all_reminders = self.store.list_reminders()
        pending = [r for r in all_reminders if r.status == "Ожидает"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].title, "Пробежка")
        self.assertEqual(pending[0].due_at, "2026-05-02T11:59:00")
        self.assertEqual(pending[0].recurrence, "Ежедневно")

    def test_weekly_recurrence_skips_seven_days(self) -> None:
        now = datetime(2026, 5, 1, 9, 0)
        self.store.add_reminder("Созвон", "", now - timedelta(minutes=1), "Еженедельно")

        self.store.process_due_reminders(now)

        pending = [r for r in self.store.list_reminders() if r.status == "Ожидает"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].due_at, "2026-05-08T08:59:00")

    def test_monthly_recurrence_clamps_to_last_day(self) -> None:
        """31 января -> 28 февраля (2026 не високосный)."""
        now = datetime(2026, 2, 1, 0, 0)
        self.store.add_reminder("Отчёт", "", datetime(2026, 1, 31, 10, 0), "Ежемесячно")

        self.store.process_due_reminders(datetime(2026, 1, 31, 10, 1))

        pending = [r for r in self.store.list_reminders() if r.status == "Ожидает"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].due_at, "2026-02-28T10:00:00")

    def test_yearly_recurrence(self) -> None:
        self.store.add_reminder("День рождения", "", datetime(2025, 6, 15, 8, 0), "Ежегодно")

        self.store.process_due_reminders(datetime(2025, 6, 15, 8, 0))

        pending = [r for r in self.store.list_reminders() if r.status == "Ожидает"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].due_at, "2026-06-15T08:00:00")

    def test_non_recurring_reminder_does_not_duplicate(self) -> None:
        now = datetime(2026, 5, 1, 12, 0)
        self.store.add_reminder("Разовое", "", now - timedelta(seconds=1))

        self.store.process_due_reminders(now)

        all_reminders = self.store.list_reminders()
        self.assertEqual(len(all_reminders), 1)
        self.assertEqual(all_reminders[0].status, "Просрочено")

    def test_invalid_recurrence_raises_error(self) -> None:
        with self.assertRaises(ValueError):
            self.store.add_reminder("Тест", "", datetime(2026, 5, 2, 9, 30), "Каждый час")

    def test_update_recurrence(self) -> None:
        """Метод update_recurrence меняет тип повтора существующего напоминания."""
        now = datetime(2026, 5, 1, 12, 0)
        reminder_id = self.store.add_reminder(
            "Тест", "", now - timedelta(minutes=1), "Ежедневно"
        )

        # Проверяем начальное значение
        reminder = [r for r in self.store.list_reminders() if r.id == reminder_id][0]
        self.assertEqual(reminder.recurrence, "Ежедневно")

        # Меняем тип повтора
        self.assertTrue(self.store.update_recurrence(reminder_id, "Еженедельно"))

        # Проверяем новое значение
        reminder = [r for r in self.store.list_reminders() if r.id == reminder_id][0]
        self.assertEqual(reminder.recurrence, "Еженедельно")

    def test_update_recurrence_invalid_value(self) -> None:
        """Недопустимое значение вызывает ValueError."""
        now = datetime(2026, 5, 1, 12, 0)
        reminder_id = self.store.add_reminder(
            "Тест", "", now - timedelta(minutes=1), "Ежедневно"
        )

        with self.assertRaises(ValueError):
            self.store.update_recurrence(reminder_id, "Каждый час")


if __name__ == "__main__":
    unittest.main()
