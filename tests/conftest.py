"""
pytest fixtures 供所有测试文件共用。
"""

import sys
import time
import types

import pytest
import tkinter as tk


def _ensure_windows_stubs():
    """
    在非 Windows 环境（如 Linux CI 调试时）插入最小 stub，
    保证 import vscode_guard 不会因缺少 Windows 模块而崩溃。
    真实 Windows 环境下这些模块已存在，stub 不会被写入。
    """
    if "win32gui" not in sys.modules:
        win32gui = types.ModuleType("win32gui")
        win32gui.EnumWindows = lambda cb, extra: None
        win32gui.IsWindowVisible = lambda hwnd: True
        win32gui.IsWindowEnabled = lambda hwnd: True
        win32gui.GetWindowText = lambda hwnd: ""
        win32gui.GetWindowPlacement = lambda hwnd: (
            0,               # flags
            1,               # showCmd (SW_SHOWNORMAL)
            (0, 0),          # ptMinPosition
            (0, 0),          # ptMaxPosition
            (0, 0, 800, 600) # rcNormalPosition
        )
        win32gui.ShowWindow = lambda hwnd, cmd: None
        win32gui.SetForegroundWindow = lambda hwnd: None
        win32gui.SendMessage = lambda hwnd, msg, wp, lp: None
        sys.modules["win32gui"] = win32gui

    if "win32con" not in sys.modules:
        win32con = types.ModuleType("win32con")
        win32con.SW_SHOWMINIMIZED = 2
        win32con.SW_RESTORE = 9
        win32con.WM_ACTIVATE = 6
        win32con.WA_CLICKACTIVE = 2
        win32con.KEYEVENTF_KEYUP = 2
        sys.modules["win32con"] = win32con

    if "win32api" not in sys.modules:
        win32api = types.ModuleType("win32api")
        win32api.keybd_event = lambda *args, **kwargs: None
        sys.modules["win32api"] = win32api

    if "winsound" not in sys.modules:
        winsound = types.ModuleType("winsound")
        winsound.PlaySound = lambda *args, **kwargs: None
        winsound.SND_MEMORY = 4
        sys.modules["winsound"] = winsound


_ensure_windows_stubs()

# 延迟导入，等 stub 注入完毕
from vscode_guard import VSCodeGuard  # noqa: E402


def pump(root: tk.Tk, n: int = 5, delay: float = 0.05) -> None:
    """驱动 Tkinter 事件循环处理积压事件。"""
    for _ in range(n):
        root.update()
        root.update_idletasks()
        time.sleep(delay)


@pytest.fixture()
def app():
    """创建 VSCodeGuard 实例，测试结束后保证清理。"""
    guard = VSCodeGuard()
    yield guard
    if guard.active:
        guard._stop()
    # 等待后台检测线程完全退出，避免线程在 root 销毁后仍调用 Tk API
    if guard.detect_thread is not None and guard.detect_thread.is_alive():
        guard.detect_thread.join(timeout=2.0)
    try:
        guard.root.destroy()
    except tk.TclError:
        pass
