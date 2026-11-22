# Known Bugs

## Wellbeing reminder popups are hidden

**Status:** Fix verified on Linux KDE Wayland and Windows. The Windows runtime
check confirmed the reminder window was visible and its action was processed.

The startup greeting appears, but Tkinter wellbeing reminders and the sample
reminder popup can remain invisible. The collector withdraws its Tk root window,
and reminder windows were made transient to that hidden root. The greeting is not
transient, so it remains visible.

On the current KDE Wayland desktop, a reminder was observed in the `withdrawn`
state with `winfo_viewable()` returning `0`. Removing its transient relationship
and deiconifying it changed its state to `normal` and `winfo_viewable()` to `1`.

**Fix:** Reminder windows are no longer transient to the withdrawn root and are
explicitly deiconified before being raised. A regression test checks this window
setup with a withdrawn root and verifies that a reminder action still works.
Restart the collector to load the updated code.
