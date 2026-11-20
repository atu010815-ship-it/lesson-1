import sqlite3
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import messagebox, ttk

from database import Reminder, ReminderStore, STATUSES, RECURRENCE_TYPES


CHECK_INTERVAL_MS = 1000


class ReminderApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.store = ReminderStore()
        self.root.title("Напоминания")
        self.root.geometry("900x570")
        self.root.minsize(720, 460)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

        self.title_var = tk.StringVar()
        self.date_var = tk.StringVar(value=datetime.now().strftime("%Y-%m-%d"))
        self.time_var = tk.StringVar(
            value=(datetime.now() + timedelta(minutes=5)).strftime("%H:%M")
        )
        self.filter_var = tk.StringVar(value="Все")
        self.recurrence_var = tk.StringVar(value="Не повторять")
        self._build_ui()
        self.refresh()
        self._check_due_reminders()

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        form = ttk.LabelFrame(self.root, text="Новое напоминание", padding=10)
        form.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 5))
        form.columnconfigure(1, weight=1)
        form.columnconfigure(3, weight=1)

        ttk.Label(form, text="Заголовок").grid(row=0, column=0, sticky="w", padx=(0, 6), pady=4)
        ttk.Entry(form, textvariable=self.title_var).grid(
            row=0, column=1, columnspan=3, sticky="ew", pady=4
        )
        ttk.Label(form, text="Дата (ГГГГ-ММ-ДД)").grid(
            row=1, column=0, sticky="w", padx=(0, 6), pady=4
        )
        ttk.Entry(form, textvariable=self.date_var, width=18).grid(
            row=1, column=1, sticky="ew", pady=4
        )
        ttk.Label(form, text="Время (ЧЧ:ММ)").grid(
            row=1, column=2, sticky="w", padx=(12, 6), pady=4
        )
        ttk.Entry(form, textvariable=self.time_var, width=12).grid(
            row=1, column=3, sticky="ew", pady=4
        )
        ttk.Label(form, text="Описание").grid(
            row=2, column=0, sticky="nw", padx=(0, 6), pady=4
        )
        self.description_text = tk.Text(form, height=3, wrap="word")
        self.description_text.grid(row=2, column=1, columnspan=3, sticky="ew", pady=4)
        ttk.Label(form, text="Повтор").grid(
            row=3, column=0, sticky="w", padx=(0, 6), pady=4
        )
        recurrence_combo = ttk.Combobox(
            form,
            textvariable=self.recurrence_var,
            values=RECURRENCE_TYPES,
            state="readonly",
            width=16,
        )
        recurrence_combo.grid(row=3, column=1, sticky="w", pady=4)
        ttk.Button(form, text="Добавить", command=self.add_reminder).grid(
            row=3, column=3, sticky="e", pady=(6, 0)
        )

        listing = ttk.Frame(self.root, padding=(10, 5, 10, 10))
        listing.grid(row=1, column=0, sticky="nsew")
        listing.columnconfigure(0, weight=1)
        listing.rowconfigure(1, weight=1)

        filter_bar = ttk.Frame(listing)
        filter_bar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(filter_bar, text="Статус:").pack(side="left")
        status_filter = ttk.Combobox(
            filter_bar,
            textvariable=self.filter_var,
            values=("Все", *STATUSES),
            state="readonly",
            width=16,
        )
        status_filter.pack(side="left", padx=(6, 0))
        status_filter.bind("<<ComboboxSelected>>", self._on_filter_change)

        table_frame = ttk.Frame(listing)
        table_frame.grid(row=1, column=0, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        columns = ("title", "due_at", "recurrence", "status", "description")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        self.tree.heading("title", text="Заголовок")
        self.tree.heading("due_at", text="Дата и время")
        self.tree.heading("recurrence", text="Повтор")
        self.tree.heading("status", text="Статус")
        self.tree.heading("description", text="Описание")
        self.tree.column("title", width=150, minwidth=100)
        self.tree.column("due_at", width=130, minwidth=120)
        self.tree.column("recurrence", width=100, minwidth=80)
        self.tree.column("status", width=90, minwidth=80)
        self.tree.column("description", width=300, minwidth=120)
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        actions = ttk.Frame(listing)
        actions.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(actions, text="Готово", command=lambda: self.set_status("Готово")).pack(
            side="left"
        )
        ttk.Button(actions, text="Отменить", command=lambda: self.set_status("Отменено")).pack(
            side="left", padx=6
        )
        ttk.Button(actions, text="Удалить", command=self.delete_reminder).pack(side="left")
        ttk.Button(actions, text="Изменить повтор", command=self.edit_recurrence).pack(
            side="left", padx=6
        )
        ttk.Button(actions, text="Обновить", command=self.refresh).pack(side="right")

    def _show_error(self, message: str) -> None:
        messagebox.showerror("Ошибка", message, parent=self.root)

    def add_reminder(self) -> None:
        try:
            due_at = datetime.strptime(
                f"{self.date_var.get().strip()} {self.time_var.get().strip()}",
                "%Y-%m-%d %H:%M",
            )
            self.store.add_reminder(
                self.title_var.get(),
                self.description_text.get("1.0", "end-1c"),
                due_at,
                self.recurrence_var.get(),
            )
        except ValueError as error:
            self._show_error(str(error) or "Введите дату и время в указанном формате.")
            return
        except sqlite3.Error as error:
            self._show_error(f"Не удалось сохранить напоминание:\n{error}")
            return

        self.title_var.set("")
        self.description_text.delete("1.0", "end")
        self.refresh()

    def refresh(self) -> None:
        try:
            reminders = self.store.list_reminders(self.filter_var.get())
        except (sqlite3.Error, ValueError) as error:
            self._show_error(f"Не удалось загрузить напоминания:\n{error}")
            return
        for item in self.tree.get_children():
            self.tree.delete(item)
        for reminder in reminders:
            self.tree.insert(
                "",
                "end",
                iid=str(reminder.id),
                values=(
                    reminder.title,
                    reminder.due_at.replace("T", " "),
                    reminder.recurrence,
                    reminder.status,
                    reminder.description,
                ),
            )

    def _on_filter_change(self, event: tk.Event) -> None:
        self.filter_var.set(event.widget.get())
        self.refresh()

    def _selected_id(self) -> int | None:
        selection = self.tree.selection()
        return int(selection[0]) if selection else None

    def set_status(self, status: str) -> None:
        reminder_id = self._selected_id()
        if reminder_id is None:
            messagebox.showinfo("Напоминания", "Сначала выберите напоминание.", parent=self.root)
            return
        try:
            self.store.set_status(reminder_id, status)
        except sqlite3.Error as error:
            self._show_error(f"Не удалось изменить статус:\n{error}")
            return
        self.refresh()

    def delete_reminder(self) -> None:
        reminder_id = self._selected_id()
        if reminder_id is None:
            messagebox.showinfo("Напоминания", "Сначала выберите напоминание.", parent=self.root)
            return
        if not messagebox.askyesno(
            "Удаление", "Удалить выбранное напоминание?", parent=self.root
        ):
            return
        try:
            self.store.delete_reminder(reminder_id)
        except sqlite3.Error as error:
            self._show_error(f"Не удалось удалить напоминание:\n{error}")
            return
        self.refresh()

    def edit_recurrence(self) -> None:
        reminder_id = self._selected_id()
        if reminder_id is None:
            messagebox.showinfo("Напоминания", "Сначала выберите напоминание.", parent=self.root)
            return

        # Получаем текущее напоминание
        all_reminders = self.store.list_reminders()
        current = next((r for r in all_reminders if r.id == reminder_id), None)
        if current is None:
            return

        # Открываем диалог
        dialog = tk.Toplevel(self.root)
        dialog.title("Изменить повтор")
        dialog.geometry("320x180")
        dialog.transient(self.root)
        dialog.grab_set()

        ttk.Label(dialog, text="Новый тип повтора для:").pack(pady=(10, 4))
        ttk.Label(dialog, text=current.title, font=("Segoe UI", 10, "bold")).pack(pady=(0, 10))

        recurrence_var = tk.StringVar(value=current.recurrence)
        combo = ttk.Combobox(
            dialog,
            textvariable=recurrence_var,
            values=RECURRENCE_TYPES,
            state="readonly",
            width=24,
        )
        combo.pack(pady=6)

        def save_and_close() -> None:
            new_value = recurrence_var.get()
            if new_value == current.recurrence:
                dialog.destroy()
                return
            try:
                self.store.update_recurrence(reminder_id, new_value)
                self.refresh()
                dialog.destroy()
            except sqlite3.Error as error:
                messagebox.showerror("Ошибка", f"Не удалось сохранить:\n{error}", parent=dialog)

        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(pady=(10, 16))
        ttk.Button(btn_frame, text="Сохранить", command=save_and_close).pack(side="left", padx=6)
        ttk.Button(btn_frame, text="Отмена", command=dialog.destroy).pack(side="left", padx=6)

        dialog.focus_force()

    def _check_due_reminders(self) -> None:
        try:
            due_reminders = self.store.process_due_reminders()
            if due_reminders:
                self.refresh()
                for reminder in due_reminders:
                    self._show_notification(reminder)
        except sqlite3.Error as error:
            self._show_error(f"Не удалось проверить напоминания:\n{error}")
        finally:
            if self.root.winfo_exists():
                self.root.after(CHECK_INTERVAL_MS, self._check_due_reminders)

    def _show_notification(self, reminder: Reminder) -> None:
        popup = tk.Toplevel(self.root)
        popup.title("Напоминание")
        popup.attributes("-topmost", True)
        popup.resizable(False, False)
        popup.transient(None)
        popup.geometry("360x190")

        content = ttk.Frame(popup, padding=16)
        content.pack(fill="both", expand=True)
        ttk.Label(content, text=reminder.title, font=("Segoe UI", 12, "bold")).pack(
            anchor="w"
        )
        description = reminder.description or "Время выполнить напоминание."
        ttk.Label(content, text=description, wraplength=325, justify="left").pack(
            anchor="w", pady=(10, 8)
        )
        ttk.Label(content, text=f"Срок: {reminder.due_at.replace('T', ' ')}").pack(
            anchor="w"
        )
        ttk.Button(content, text="Закрыть", command=popup.destroy).pack(
            anchor="e", pady=(12, 0)
        )
        popup.lift()
        popup.focus_force()
        popup.attributes("-topmost", True)

    def _close(self) -> None:
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    ReminderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
