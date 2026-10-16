# WASD Snap Tap

A Windows Python GUI prototype for WASD last-input priority on any keyboard that doesn't have last input priority.

## What currently works

- The GUI opens and can start or stop the low-level keyboard hook.
- The hook detects physical W, A, S, and D presses and the GUI highlights the effective direction.
- The intended rule is last-input priority: if A is held and D is pressed, D takes priority; releasing D restores A. W/S follows the same rule.
- When enabled, a fresh Space tap passes through normally and triggers a V tap after the configured delay. Delay and V hold length are adjustable from 1 to 250 ms.
- If Windows rejects a synthesized key event, the app fails open and lets physical WASD pass through so movement is restored.

## Current status

- `SendInput` is now using the native 64-bit `INPUT` layout: `MOUSEINPUT`/`KEYBDINPUT`/`HARDWAREINPUT` with `ULONG_PTR`-sized extra-info fields, for a 40-byte structure. Direct test calls to `SendInput` return success (`0` failures over `1000` attempts) and no `ERROR_INVALID_PARAMETER`.
- The app still fails open if any replacement event is rejected, so live Snap Tap behavior should be verified with a physical keyboard.
- The app does not configure Logitech G HUB or change keyboard firmware settings. Its rapid-release behavior depends on getting replacement key events accepted by Windows first.

## Run

1. Open `dist/wasd_snap_tap.exe` for the packaged, no-console app.
2. To run from source without a console, install Python 3.10 or newer with Tcl/Tk support, then double-click `wasd_snap_tap.pyw` (or run `pythonw wasd_snap_tap.py`).
3. To rebuild the packaged app, run `pyinstaller --clean --noconfirm wasd_snap_tap.spec` from this folder. The build embeds `wasd_snap_tap.ico` and does not open a console window.
4. Enable **keyboard helper** to start the hook. The Space-to-V feature is enabled by default and can be toggled separately. If Windows rejects synthesized events, the app displays the error and stops intercepting WASD.
