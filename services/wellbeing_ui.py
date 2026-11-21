from __future__ import annotations

import queue
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
        self._window = None
        self._prompt: WellbeingPrompt | None = None
        self._selected_exercise = None

    def show_greeting(self, hour: int) -> None:
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
        self._ttk.Label(frame, text="WorkGraph activity tracking is running.").grid(
            row=1, column=0, sticky="w", pady=(0, 12)
        )
        self._ttk.Button(frame, text="Dismiss", command=window.destroy).grid(
            row=2, column=0, sticky="e"
        )
        window.lift()

    def show(self, prompt: WellbeingPrompt) -> None:
        if self._window is not None and self._prompt == prompt:
            return
        if self._window is not None:
            self._window.destroy()

        window = self._tk.Toplevel(self.root)
        window.title(prompt.title)
        window.resizable(False, False)
        window.protocol("WM_DELETE_WINDOW", self._ignore_close)
        window.bind("<Escape>", self._ignore_close)
        window.transient(self.root)
        self._window = window
        self._prompt = prompt

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

        if prompt.kind == "idle":
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
            self._selected_exercise = self._tk.StringVar(
                value=prompt.exercises[0].name if prompt.exercises else ""
            )
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
                    variable=self._selected_exercise,
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
                ((f"Snooze {self.settings.water_snooze_minutes}m", "water_snooze"),),
                row=2,
            )

        window.update_idletasks()
        window.grab_set()
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
        exercise_name = (
            self._selected_exercise.get()
            if action == "workout_complete" and self._selected_exercise is not None
            else None
        )
        try:
            self.action_queue.put_nowait((action, exercise_name))
        except queue.Full:
            return False
        if self._window is not None:
            self._window.grab_release()
            self._window.destroy()
        self._window = None
        self._prompt = None
        return True

    def close(self) -> None:
        if self._window is not None:
            self._window.grab_release()
            self._window.destroy()
        self._window = None
        self._prompt = None

    @staticmethod
    def _ignore_close(_event=None) -> str:
        return "break"
