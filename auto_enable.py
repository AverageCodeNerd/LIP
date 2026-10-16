"""WASD last-input-priority helper for Windows."""

import ctypes
import threading
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
VK = {"A": 0x41, "D": 0x44, "W": 0x57, "S": 0x53}
PAIRS = (("A", "D"), ("W", "S"))


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

    def _send(self, key, down):
        inp = INPUT(type=INPUT_KEYBOARD, union=INPUT_UNION(ki=KEYBDINPUT(
            wVk=VK[key], wScan=0, dwFlags=0 if down else KEYEVENTF_KEYUP,
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

    def _fail_open(self):
        for key in list(self.output):
            self._send(key, False)
        self.output.clear()
        self.physical.clear()
        self.order.clear()
        self.pass_through = True
        self.enabled = False
        if self.thread_id:
            user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)
        self.on_change(("sendinput", self.send_error), None)

    def _proc(self, n_code, w_param, l_param):
        if self.pass_through:
            return user32.CallNextHookEx(self.hook, n_code, w_param, l_param)
        if n_code >= 0 and w_param in (WM_KEYDOWN, WM_SYSKEYDOWN, WM_KEYUP, WM_SYSKEYUP):
            data = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            key = next((k for k, code in VK.items() if code == data.vkCode), None)
            if key and not (data.flags & LLKHF_INJECTED):
                with self.lock:
                    down = w_param in (WM_KEYDOWN, WM_SYSKEYDOWN)
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
        root.geometry("540x390")
        root.resizable(False, False)
        root.configure(bg="#111827")
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background="#111827")
        style.configure("TLabel", background="#111827", foreground="#e5e7eb", font=("Segoe UI", 10))
        style.configure("Title.TLabel", font=("Segoe UI", 20, "bold"), foreground="#ffffff")
        style.configure("TButton", font=("Segoe UI", 11, "bold"), padding=10)
        style.configure("TCheckbutton", background="#111827", foreground="#e5e7eb", font=("Segoe UI", 10))
        frame = ttk.Frame(root, padding=24)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="WASD Snap Tap", style="Title.TLabel").pack(anchor="w")
        ttk.Label(frame, text="Last pressed direction wins; releasing it restores the held key.",
                  wraplength=480).pack(anchor="w", pady=(8, 8))
        ttk.Label(frame, text="Key-up is forwarded immediately for rapid release response.",
                  foreground="#93c5fd").pack(anchor="w", pady=(0, 18))
        self.enabled_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text="Enable WASD last-input priority", variable=self.enabled_var,
                        command=self.toggle).pack(anchor="w", pady=(0, 18))
        self.keys = {}
        keyrow = ttk.Frame(frame)
        keyrow.pack(pady=4)
        for key in ("W", "A", "S", "D"):
            label = tk.Label(keyrow, text=key, width=4, height=2, bg="#263244", fg="#94a3b8",
                             font=("Segoe UI", 14, "bold"), relief="flat")
            label.pack(side="left", padx=4)
            self.keys[key] = label
        self.status = ttk.Label(frame, text="Off", foreground="#fca5a5")
        self.status.pack(anchor="w", pady=(18, 4))
        ttk.Label(frame, text="A/D and W/S are resolved independently. Close this window to stop.",
                  wraplength=480).pack(anchor="w", pady=(8, 0))
        self.engine = SnapTap(self.update_keys)
        root.after(200, self.engine.start)
        root.protocol("WM_DELETE_WINDOW", self.close)

    def toggle(self):
        if self.enabled_var.get():
            self.status.configure(text="Active — key releases are passed through immediately", foreground="#86efac")
            self.engine.start()
        else:
            self.engine.stop()
            self.status.configure(text="Off", foreground="#fca5a5")

    def update_keys(self, physical, effective):
        if physical == "hook" or (isinstance(physical, tuple) and physical[0] == "sendinput"):
            message = ("Could not install keyboard hook" if physical == "hook" else
                       f"Replacement keys failed (Windows error {physical[1]}); WASD is passing through.")
            self.root.after(0, lambda: self.status.configure(text=message, foreground="#fca5a5"))
            self.root.after(0, lambda: self.enabled_var.set(False))
            return
        def paint():
            for key, label in self.keys.items():
                label.configure(bg="#2563eb" if key in effective else "#263244",
                                fg="#ffffff" if key in effective else "#94a3b8")
        self.root.after(0, paint)

    def close(self):
        self.engine.stop()
        self.root.destroy()


if __name__ == "__main__":
    window = tk.Tk()
    App(window)
    window.mainloop()
