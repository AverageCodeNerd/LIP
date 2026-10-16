"""WASD last-input-priority helper for Windows."""

import ctypes
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
ULONG_PTR = ctypes.c_size_t
LLKHF_INJECTED = 0x10
WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105
INPUT_KEYBOARD, KEYEVENTF_KEYUP = 1, 0x0002
VK = {"A": 0x41, "D": 0x44, "W": 0x57, "S": 0x53, "V": 0x56}
SPACE_VK = 0x20
FRAME_SECONDS = 1 / 120
PAIRS = (("A", "D"), ("W", "S"))


def asset_path(filename):
    base_path = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, filename)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", INPUT_UNION)]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int,
                              wintypes.WPARAM, wintypes.LPARAM)
user32.SetWindowsHookExW.argtypes = (ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD)
user32.SetWindowsHookExW.restype = wintypes.HANDLE
user32.CallNextHookEx.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.UnhookWindowsHookEx.argtypes = (wintypes.HANDLE,)
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT
user32.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
user32.GetMessageW.restype = wintypes.BOOL
user32.TranslateMessage.argtypes = (ctypes.POINTER(wintypes.MSG),)
user32.DispatchMessageW.argtypes = (ctypes.POINTER(wintypes.MSG),)
user32.PostThreadMessageW.argtypes = (wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.PostThreadMessageW.restype = wintypes.BOOL
kernel32.GetCurrentThreadId.restype = wintypes.DWORD


class SnapTap:
    def __init__(self, on_change):
        self.on_change = on_change
        self.physical = set()
        self.order = []
        self.output = set()
        self.enabled = False
        self.hook = None
        self.thread_id = None
        self.callback = None
        self.pass_through = False
        self.send_error = 0
        self.lock = threading.RLock()
        self.space_v_enabled = True
        self.space_down = False
        self.v_down = False
        self.timers = set()
        self.v_delay_ms = FRAME_SECONDS * 1000
        self.v_hold_ms = FRAME_SECONDS * 1000

    def _send(self, key, down):
        scan = user32.MapVirtualKeyW(VK[key], 0)
        inp = INPUT(type=INPUT_KEYBOARD, union=INPUT_UNION(ki=KEYBDINPUT(
            wVk=VK[key], wScan=scan, dwFlags=0 if down else KEYEVENTF_KEYUP,
            time=0, dwExtraInfo=0)))
        ctypes.set_last_error(0)
        sent = user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
        self.send_error = ctypes.get_last_error()
        return sent == 1

    def _desired(self):
        desired = self.physical.copy()
        for left, right in PAIRS:
            if left in self.physical and right in self.physical:
                last = next((k for k in reversed(self.order) if k in (left, right)), left)
                desired.discard(right if last == left else left)
        return desired

    def _transition(self):
        desired = self._desired()
        for key in self.output - desired:
            if not self._send(key, False):
                return False
            self.output.discard(key)
        for key in desired - self.output:
            if not self._send(key, True):
                return False
            self.output.add(key)
        self.on_change(self.physical.copy(), desired.copy())
        return True

    def _schedule_v_tap(self, delay):
        timer = threading.Timer(delay, self._press_v)
        timer.daemon = True
        self.timers.add(timer)
        timer.start()

    def _press_v(self):
        with self.lock:
            self.timers.discard(threading.current_thread())
            if not self.enabled or self.pass_through or not self.space_v_enabled:
                return
            if self.v_down:
                self._schedule_v_tap(self.v_delay_ms / 1000)
                return
            if not self._send("V", True):
                self._fail_open()
                return
            self.v_down = True
            timer = threading.Timer(self.v_hold_ms / 1000, self._release_v)
            timer.daemon = True
            self.timers.add(timer)
            timer.start()

    def _release_v(self):
        with self.lock:
            self.timers.discard(threading.current_thread())
            if not self.v_down:
                return
            if not self._send("V", False):
                self._fail_open()
                return
            self.v_down = False

    def _cancel_timers(self):
        for timer in self.timers:
            timer.cancel()
        self.timers.clear()

    def _fail_open(self):
        send_error = self.send_error
        self._cancel_timers()
        if self.v_down:
            self._send("V", False)
            self.v_down = False
        for key in list(self.output):
            self._send(key, False)
        self.output.clear()
        self.physical.clear()
        self.order.clear()
        self.space_down = False
        self.pass_through = True
        self.enabled = False
        if self.thread_id:
            user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)
        self.on_change(("sendinput", send_error), None)

    def _proc(self, n_code, w_param, l_param):
        if self.pass_through:
            return user32.CallNextHookEx(self.hook, n_code, w_param, l_param)
        if n_code >= 0 and w_param in (WM_KEYDOWN, WM_SYSKEYDOWN, WM_KEYUP, WM_SYSKEYUP):
            data = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            down = w_param in (WM_KEYDOWN, WM_SYSKEYDOWN)
            if data.vkCode == SPACE_VK and not (data.flags & LLKHF_INJECTED):
                with self.lock:
                    if down and not self.space_down:
                        self.space_down = True
                        if self.space_v_enabled:
                            self._schedule_v_tap(self.v_delay_ms / 1000)
                    elif not down:
                        self.space_down = False
                return user32.CallNextHookEx(self.hook, n_code, w_param, l_param)
            key = next((k for k, code in VK.items() if code == data.vkCode), None)
            if key in ("A", "D", "W", "S") and not (data.flags & LLKHF_INJECTED):
                with self.lock:
                    if down and key not in self.physical:
                        self.physical.add(key)
                        self.order.append(key)
                    elif not down:
                        self.physical.discard(key)
                        self.order = [k for k in self.order if k != key]
                    if not self._transition():
                        self._fail_open()
                        return user32.CallNextHookEx(self.hook, n_code, w_param, l_param)
                return 1
        return user32.CallNextHookEx(self.hook, n_code, w_param, l_param)

    def _run(self):
        self.thread_id = kernel32.GetCurrentThreadId()
        self.callback = HOOKPROC(self._proc)
        self.hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self.callback, None, 0)
        if not self.hook:
            self.enabled = False
            self.on_change("hook", None)
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        user32.UnhookWindowsHookEx(self.hook)
        self.hook = None

    def start(self):
        if self.enabled:
            return
        self.enabled = True
        self.pass_through = False
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self.enabled = False
        if self.thread_id:
            user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)
        with self.lock:
            self._cancel_timers()
            if self.v_down:
                self._send("V", False)
                self.v_down = False
            self.space_down = False
            for key in list(self.output):
                self._send(key, False)
            self.output.clear()
            self.physical.clear()
            self.order.clear()
        self.on_change(set(), set())


class App:
    def __init__(self, root):
        self.root = root
        root.title("WASD Snap Tap")
        root.geometry("900x700")
        root.minsize(840, 620)
        root.configure(bg="#03070d")
        try:
            root.iconbitmap(asset_path("wasd_snap_tap.ico"))
        except tk.TclError:
            pass
        self.colors = {
            "background": "#03070d",
            "panel": "#071421",
            "panel_lift": "#0b2033",
            "key": "#102b43",
            "accent": "#2589d5",
            "accent_bright": "#59baff",
            "ice": "#e7f7ff",
            "text": "#f3f8fc",
            "muted": "#9bb6c9",
            "green": "#79e3c3",
            "red": "#ff8191",
            "amber": "#f6d38b",
        }
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background=self.colors["background"])
        style.configure("TLabel", background=self.colors["background"],
                        foreground=self.colors["text"], font=("Segoe UI", 10))
        style.configure("Title.TLabel", font=("Segoe UI", 20, "bold"),
                        foreground=self.colors["ice"])
        style.configure("Subtitle.TLabel", font=("Segoe UI", 9),
                        foreground=self.colors["muted"])
        style.configure("Eyebrow.TLabel", font=("Segoe UI", 9, "bold"),
                        foreground=self.colors["accent"])
        style.configure("CardEyebrow.TLabel", background=self.colors["panel"],
                        foreground=self.colors["accent_bright"], font=("Segoe UI", 9, "bold"))
        style.configure("CardSubtitle.TLabel", background=self.colors["panel"],
                        foreground=self.colors["muted"], font=("Segoe UI", 9))
        style.configure("Toggle.TCheckbutton", background=self.colors["panel"],
                        foreground=self.colors["text"], font=("Segoe UI", 11, "bold"),
                        padding=4)
        style.map("Toggle.TCheckbutton",
                  background=[("active", self.colors["panel"])],
                  foreground=[("disabled", self.colors["muted"])])

        frame = tk.Frame(root, bg=self.colors["background"], padx=28, pady=22)
        frame.pack(fill="both", expand=True)

        # Top bar: product identity, live clock, and a compact state indicator.
        header = tk.Frame(frame, bg=self.colors["background"])
        header.pack(fill="x", pady=(0, 20))
        brand = tk.Frame(header, bg=self.colors["background"])
        brand.pack(side="left", fill="x", expand=True)
        tk.Label(brand, text="OCEAN  /  INPUT CONTROL", bg=self.colors["background"],
                 fg=self.colors["accent_bright"], font=("Segoe UI", 9, "bold")).pack(anchor="w")
        tk.Label(brand, text="WASD Snap Tap", bg=self.colors["background"],
                 fg=self.colors["ice"], font=("Segoe UI", 22, "bold")).pack(anchor="w", pady=(2, 0))
        tk.Label(brand, text="LAST-INPUT PRIORITY SYSTEM", bg=self.colors["background"],
                 fg=self.colors["muted"], font=("Segoe UI", 9)).pack(anchor="w", pady=(1, 0))
        clock_box = tk.Frame(header, bg=self.colors["background"])
        clock_box.pack(side="right", padx=(12, 20))
        self.clock = tk.Label(clock_box, text="--:--", bg=self.colors["background"],
                              fg=self.colors["accent"], font=("Segoe UI Light", 32))
        self.clock.pack(anchor="e")
        tk.Label(clock_box, text="LOCAL TIME", bg=self.colors["background"],
                 fg=self.colors["muted"], font=("Segoe UI", 8, "bold")).pack(anchor="e")
        wave = tk.Canvas(header, width=52, height=56, bg=self.colors["background"], highlightthickness=0)
        wave.pack(side="right", padx=(0, 18))
        wave.create_line(-4, 43, 7, 40, 16, 26, 26, 31, 34, 13, 43, 18, 56, 5,
                         smooth=True, splinesteps=24, fill=self.colors["accent"], width=5)
        wave.create_line(-4, 50, 7, 47, 17, 38, 27, 42, 36, 30, 45, 33, 56, 24,
                         smooth=True, splinesteps=24, fill=self.colors["ice"], width=2)
        self.status = tk.Label(header, text="●  OFF", bg=self.colors["panel_lift"],
                               fg=self.colors["muted"], font=("Segoe UI", 9, "bold"),
                               padx=13, pady=8)
        self.status.pack(side="right", padx=(0, 4))
        tk.Frame(frame, bg="#12314b", height=1).pack(fill="x", pady=(0, 20))

        # Two-column dashboard keeps controls separate from the live key monitor.
        body = tk.Frame(frame, bg=self.colors["background"])
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(0, weight=4, uniform="dashboard")
        body.grid_columnconfigure(1, weight=6, uniform="dashboard")
        body.grid_rowconfigure(0, weight=1)
        card_style = {"bg": self.colors["panel"], "highlightthickness": 1,
                      "highlightbackground": "#12314b"}

        controls = tk.Frame(body, padx=22, pady=22, **card_style)
        controls.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        tk.Label(controls, text="CONTROL CENTER", bg=self.colors["panel"],
                 fg=self.colors["accent_bright"], font=("Segoe UI", 9, "bold")).pack(anchor="w")
        tk.Label(controls, text="Configure your\ninput profile", bg=self.colors["panel"],
                 fg=self.colors["ice"], font=("Segoe UI", 18, "bold"), justify="left").pack(anchor="w", pady=(8, 4))
        tk.Label(controls, text="Changes take effect while the helper is active.",
                 bg=self.colors["panel"], fg=self.colors["muted"],
                 font=("Segoe UI", 9), wraplength=270, justify="left").pack(anchor="w", pady=(0, 24))
        tk.Frame(controls, bg="#12314b", height=1).pack(fill="x", pady=(0, 18))
        self.enabled_var = tk.BooleanVar(value=False)
        self.space_v_var = tk.BooleanVar(value=True)
        self.delay_var = tk.StringVar(value="8")
        self.hold_var = tk.StringVar(value="8")
        control_copy = tk.Frame(controls, bg=self.colors["panel"])
        control_copy.pack(fill="x")
        ttk.Checkbutton(control_copy, text="Enable keyboard helper", variable=self.enabled_var,
                        command=self.toggle, style="Toggle.TCheckbutton").pack(anchor="w")
        tk.Label(controls, text="DIRECTION RESOLUTION", bg=self.colors["panel"],
                 fg=self.colors["muted"], font=("Segoe UI", 8, "bold")).pack(anchor="w", pady=(20, 5))
        tk.Label(controls, text="Last pressed direction wins. Releasing it restores the other held key.",
                 bg=self.colors["panel"], fg=self.colors["text"], font=("Segoe UI", 9),
                 wraplength=270, justify="left").pack(anchor="w")
        tk.Frame(controls, bg="#12314b", height=1).pack(fill="x", pady=(18, 14))
        tk.Label(controls, text="OPTIONAL ACTION", bg=self.colors["panel"],
                 fg=self.colors["muted"], font=("Segoe UI", 8, "bold")).pack(anchor="w", pady=(0, 5))
        ttk.Checkbutton(controls, text="Space → V superglide tap", variable=self.space_v_var,
                        command=self.toggle_space_v, style="Toggle.TCheckbutton").pack(anchor="w")
        tk.Label(controls, text="Set the delay before V presses and how long V stays down.",
                 bg=self.colors["panel"], fg=self.colors["muted"], font=("Segoe UI", 9),
                 wraplength=270, justify="left").pack(anchor="w", padx=(4, 0), pady=(5, 0))
        timing = tk.Frame(controls, bg=self.colors["panel"])
        timing.pack(fill="x", pady=(12, 0))
        for col in range(2):
            timing.grid_columnconfigure(col, weight=1)
        self._timing_control(timing, "DELAY", self.delay_var, 0)
        self._timing_control(timing, "V HOLD", self.hold_var, 1)

        activity = tk.Frame(body, padx=24, pady=22, **card_style)
        activity.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        activity.grid_rowconfigure(2, weight=1)
        tk.Label(activity, text="LIVE INPUT MONITOR", bg=self.colors["panel"],
                 fg=self.colors["accent_bright"], font=("Segoe UI", 9, "bold")).pack(anchor="w")
        monitor_heading = tk.Frame(activity, bg=self.colors["panel"])
        monitor_heading.pack(fill="x", pady=(7, 0))
        tk.Label(monitor_heading, text="Movement keys", bg=self.colors["panel"],
                 fg=self.colors["ice"], font=("Segoe UI", 17, "bold")).pack(side="left")
        tk.Label(monitor_heading, text="WASD", bg=self.colors["panel_lift"],
                 fg=self.colors["accent_bright"], font=("Segoe UI", 8, "bold"),
                 padx=9, pady=5).pack(side="right")
        self.detail = tk.Label(activity, text="Enable the helper to start monitoring input.",
                               bg=self.colors["panel"], fg=self.colors["muted"],
                               font=("Segoe UI", 9), anchor="w")
        self.detail.pack(anchor="w", pady=(4, 8))

        visual = tk.Frame(activity, bg="#050c14", padx=20, pady=18,
                          highlightthickness=1, highlightbackground="#102b43")
        visual.pack(fill="both", expand=True, pady=(6, 14))
        tk.Label(visual, text="CURRENT INPUT", bg="#050c14", fg=self.colors["muted"],
                 font=("Segoe UI", 8, "bold")).pack(anchor="w")
        self.input_state = tk.Label(visual, text="No movement input", bg="#050c14",
                                    fg=self.colors["ice"], font=("Segoe UI", 20, "bold"))
        self.input_state.pack(anchor="w", pady=(4, 18))
        self.pair_status = {}
        for pair, title in (("AD", "HORIZONTAL  ·  A / D"), ("WS", "VERTICAL  ·  W / S")):
            lane = tk.Frame(visual, bg=self.colors["panel"], padx=14, pady=12)
            lane.pack(fill="x", pady=5)
            tk.Label(lane, text=title, bg=self.colors["panel"], fg=self.colors["muted"],
                     font=("Segoe UI", 8, "bold")).pack(side="left")
            state = tk.Label(lane, text="IDLE", bg=self.colors["panel_lift"],
                             fg=self.colors["muted"], font=("Segoe UI", 9, "bold"),
                             padx=10, pady=5)
            state.pack(side="right")
            self.pair_status[pair] = state
        tk.Label(activity, text="Held inputs and the winning direction update live while enabled.",
                 bg=self.colors["panel"], fg=self.colors["muted"],
                 font=("Segoe UI", 8)).pack(anchor="w")

        footer = tk.Frame(frame, bg=self.colors["background"])
        footer.pack(fill="x", pady=(16, 0))
        tk.Label(footer, text="INPUT ENGINE  ·  READY", bg=self.colors["background"],
                 fg=self.colors["muted"], font=("Segoe UI", 8, "bold")).pack(side="left")
        tk.Label(footer, text="Close this window to stop the keyboard hook", bg=self.colors["background"],
                 fg=self.colors["muted"], font=("Segoe UI", 8)).pack(side="right")
        self.engine = SnapTap(self.update_keys)
        self.update_timing()
        self._tick_clock()
        root.protocol("WM_DELETE_WINDOW", self.close)

    def _tick_clock(self):
        self.clock.configure(text=time.strftime("%H:%M"))
        self.root.after(1000, self._tick_clock)

    def _timing_control(self, parent, label, variable, column):
        box = tk.Frame(parent, bg=self.colors["panel"])
        box.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 8, 0))
        tk.Label(box, text=label, bg=self.colors["panel"], fg=self.colors["muted"],
                 font=("Segoe UI", 8, "bold")).pack(anchor="w")
        field = tk.Spinbox(box, from_=1, to=250, increment=1, textvariable=variable,
                           width=7, command=self.update_timing, relief="flat", bd=0,
                           bg=self.colors["panel_lift"], fg=self.colors["ice"],
                           buttonbackground=self.colors["accent"], insertbackground=self.colors["ice"],
                           font=("Segoe UI", 10), highlightthickness=1,
                           highlightbackground="#1b4362", highlightcolor=self.colors["accent"])
        field.pack(anchor="w", pady=(5, 0))
        field.bind("<FocusOut>", lambda _event: self.update_timing())
        field.bind("<Return>", lambda _event: self.update_timing())
        tk.Label(box, text="milliseconds", bg=self.colors["panel"], fg=self.colors["muted"],
                 font=("Segoe UI", 8)).pack(anchor="w", pady=(3, 0))

    def update_timing(self):
        try:
            delay = min(250, max(1, int(self.delay_var.get())))
            hold = min(250, max(1, int(self.hold_var.get())))
        except ValueError:
            return
        self.delay_var.set(str(delay))
        self.hold_var.set(str(hold))
        self.engine.v_delay_ms = delay
        self.engine.v_hold_ms = hold

    def toggle(self):
        if self.enabled_var.get():
            self.status.configure(text="●  ACTIVE", fg=self.colors["green"])
            self.detail.configure(
                text="Press opposing directions or tap Space to trigger V.",
                fg=self.colors["muted"],
            )
            self.engine.start()
        else:
            self.engine.stop()
            self.status.configure(text="●  OFF", fg=self.colors["muted"])
            self.detail.configure(
                text="Enable the helper below to start monitoring input.",
                fg=self.colors["muted"],
            )

    def toggle_space_v(self):
        self.engine.space_v_enabled = self.space_v_var.get()

    def update_keys(self, physical, effective):
        if physical == "hook" or (isinstance(physical, tuple) and physical[0] == "sendinput"):
            message = ("Could not install keyboard hook" if physical == "hook" else
                       f"Replacement keys failed (Windows error {physical[1]}); WASD is passing through.")
            def show_error():
                self.status.configure(text="●  ERROR", fg=self.colors["red"])
                self.detail.configure(text=message, fg=self.colors["red"])
                self.enabled_var.set(False)
            self.root.after(0, show_error)
            return

        def paint():
            held = " + ".join(sorted(physical))
            winners = " + ".join(sorted(effective))
            if not held:
                self.input_state.configure(text="No movement input", fg=self.colors["muted"])
            else:
                self.input_state.configure(text=f"Held  {held}   ·   Active  {winners}",
                                           fg=self.colors["ice"])
            for pair, keys in (("AD", ("A", "D")), ("WS", ("W", "S"))):
                active = [key for key in keys if key in physical]
                winner = [key for key in keys if key in effective]
                if not active:
                    text, color = "IDLE", self.colors["muted"]
                elif len(active) == 1:
                    text, color = f"{active[0]} ACTIVE", self.colors["accent_bright"]
                else:
                    text, color = f"{winner[0]} PRIORITY", self.colors["ice"]
                self.pair_status[pair].configure(text=text, fg=color)
        self.root.after(0, paint)

    def close(self):
        self.engine.stop()
        self.root.destroy()


if __name__ == "__main__":
    window = tk.Tk()
    App(window)
    window.mainloop()
