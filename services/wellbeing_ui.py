from __future__ import annotations

import queue
from collections.abc import Callable
from queue import Queue

from services.wellbeing import WellbeingPrompt, WellbeingSettings


class TkWellbeingUI:
    def __init__(
        self,
        root,
        action_queue: Queue[tuple[str, str | None]],
        settings: WellbeingSettings,
    ) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.root = root
        self.action_queue = action_queue
        self.settings = settings
        self._tk = tk
        self._ttk = ttk
        self._windows: dict[str, object] = {}
        self._selected_exercises: dict[str, object] = {}

    def show_greeting(
        self, hour: int, statuses: Callable[[], list[tuple[str, str]]] | None = None
    ) -> None:
        if 5 <= hour < 12:
            greeting = "Good morning"
        elif 12 <= hour < 17:
            greeting = "Good afternoon"
        else:
            greeting = "Good evening"

        window = self._tk.Toplevel(self.root)
        window.title("WorkGraph")
        window.resizable(False, False)
        frame = self._ttk.Frame(window, padding=18)
        frame.grid(sticky="nsew")
        self._ttk.Label(
            frame, text=f"{greeting}!", font=("TkDefaultFont", 15, "bold")
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))
        self._ttk.Label(frame, text="WorkGraph collector is running.").grid(
            row=1, column=0, sticky="w", pady=(0, 12)
        )
        rows = {}
        initial_statuses = statuses() if statuses else []
        for row, (name, status) in enumerate(initial_statuses, start=2):
            name_label = self._ttk.Label(frame)
            name_label.grid(row=row, column=0, sticky="w", padx=(0, 20), pady=2)
            status_label = self._ttk.Label(frame)
            status_label.grid(row=row, column=1, sticky="w", pady=2)
            rows[name] = (name_label, status_label)

        def refresh_statuses() -> None:
            if statuses is None:
                return
            for name, status in statuses():
                if name not in rows:
                    continue
                marker = (
                    "\u2713"
                    if status in {"Running", "Enabled", "Process running", "Completed"}
                    else "\u2717"
                )
                rows[name][0].configure(text=f"{marker}  {name}")
                rows[name][1].configure(text=status)
            window.after(5000, refresh_statuses)

        if statuses:
            refresh_statuses()
        self._ttk.Button(frame, text="Dismiss", command=window.destroy).grid(
            row=2 + len(initial_statuses), column=1, sticky="e", pady=(12, 0)
        )
        window.lift()

    def show(self, prompt: WellbeingPrompt) -> None:
        if prompt.kind in self._windows:
            return

        window = self._tk.Toplevel(self.root)
        window.title(prompt.title)
        window.resizable(False, False)
        window.protocol("WM_DELETE_WINDOW", self._ignore_close)
        window.bind("<Escape>", self._ignore_close)
        self._windows[prompt.kind] = window

        frame = self._ttk.Frame(window, padding=18)
        frame.grid(sticky="nsew")
        self._ttk.Label(
            frame,
            text=prompt.title,
            font=("TkDefaultFont", 15, "bold"),
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))
        self._ttk.Label(
            frame,
            text=prompt.message,
            wraplength=360,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(0, 12))

        if prompt.kind == "break":
            self._button_row(
                frame,
                (
                    (f"Snooze {self.settings.break_snooze_minutes}m", "break_snooze"),
                    ("Break taken", "break_taken"),
                ),
                row=2,
            )
        elif prompt.kind == "idle":
            self._button_row(
                frame,
                (
                    ("Taking a break", "idle_break"),
                    ("Still working", "idle_working"),
                    ("Snooze", "idle_snooze"),
                ),
                row=2,
            )
        elif prompt.kind == "workout":
            selected_exercise = self._tk.StringVar(
                value=prompt.exercises[0].name if prompt.exercises else ""
            )
            self._selected_exercises[prompt.kind] = selected_exercise
            for index, exercise in enumerate(prompt.exercises, start=2):
                quantity = (
                    f"{exercise.reps} reps"
                    if exercise.reps is not None
                    else f"{exercise.duration_seconds} seconds"
                )
                self._ttk.Radiobutton(
                    frame,
                    text=f"{exercise.name} ({quantity})",
                    value=exercise.name,
                    variable=selected_exercise,
                ).grid(row=index, column=0, sticky="w", pady=2)
            self._button_row(
                frame,
                (
                    ("Snooze", "workout_snooze"),
                    ("Workout complete", "workout_complete"),
                    ("Skip for today", "workout_skip"),
                ),
                row=len(prompt.exercises) + 2,
                spaced=True,
            )
        else:
            self._button_row(
                frame,
                (
                    (f"Snooze {self.settings.water_snooze_minutes}m", "water_snooze"),
                    ("Completed", "water_complete"),
                ),
                row=2,
            )

        window.update_idletasks()
        window.deiconify()
        window.lift()

    def _button_row(
        self,
        frame,
        buttons: tuple[tuple[str, str], ...],
        *,
        row: int,
        spaced: bool = False,
    ) -> None:
        button_frame = self._ttk.Frame(frame)
        button_frame.grid(row=row, column=0, sticky="e", pady=(8, 0))
        for column, (label, action) in enumerate(buttons):
            button = self._ttk.Button(
                button_frame,
                text=label,
                command=lambda selected_action=action: self._submit_action(
                    selected_action
                ),
            )
            button.grid(row=0, column=column, padx=(8 if spaced or column else 0, 0))

    def _submit_action(self, action: str) -> bool:
        kind = action.partition("_")[0]
        selected_exercise = self._selected_exercises.get(kind)
        exercise_name = (
            selected_exercise.get()
            if action == "workout_complete" and selected_exercise is not None
            else None
        )
        try:
            self.action_queue.put_nowait((action, exercise_name))
        except queue.Full:
            return False
        window = self._windows.pop(kind, None)
        if window is not None:
            window.destroy()
        self._selected_exercises.pop(kind, None)
        return True

    def close(self) -> None:
        for window in self._windows.values():
            window.destroy()
        self._windows.clear()
        self._selected_exercises.clear()

    @staticmethod
    def _ignore_close(_event=None) -> str:
        return "break"
