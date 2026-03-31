"""
pytest fixtures 供所有测试文件共用。
"""

import os
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


def pump(root: tk.Misc, n: int = 5, delay: float = 0.05) -> None:
    """驱动 Tkinter 事件循环处理积压事件。"""
    for _ in range(n):
        root.update()
        root.update_idletasks()
        time.sleep(delay)


@pytest.fixture(scope="session")
def _tk_session_root():
    """
    整个测试 session 共享唯一一个 tk.Tk() 根窗口。

    背景：在 GitHub Actions 的 Windows runner（Python 3.11.9 toolcache）中，
    Tcl/Tk 脚本文件（如 ttk/cursors.tcl）不完整。第一次创建 tk.Tk() 时，
    Tcl 解释器从磁盘加载脚本并缓存到内存；destroy() 后再次创建新解释器时，
    需重新从磁盘加载，此时发现文件缺失而报错。
    通过在整个 session 只创建一次 Tk 根窗口，并在每个测试中使用 Toplevel
    子窗口，可完全规避该问题。
    """
    root = tk.Tk()
    root.withdraw()  # 隐藏根窗口，只显示各测试的 Toplevel 子窗口
    yield root
    try:
        root.destroy()
    except tk.TclError:
        pass


@pytest.fixture()
def app(_tk_session_root):
    """
    创建 VSCodeGuard 实例，测试结束后保证清理。
    使用共享的 session 级 Tk 根窗口，每次测试创建 Toplevel 子窗口，
    避免多次 Tcl 解释器初始化/销毁导致的 TclError。
    """
    guard = VSCodeGuard(_root=_tk_session_root)
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


@pytest.fixture(autouse=True)
def screenshot_on_failure(request):
    """
    测试失败时自动截图并保存到 test-screenshots/ 目录。
    截图文件以测试名称命名，便于 CI artifact 上传后定位问题。
    优先捕获 app fixture 对应的 Tkinter 窗口区域；否则回退到全屏截图。
    """
    yield
    rep_call = getattr(request.node, "rep_call", None)
    if rep_call is not None and rep_call.failed:
        widget = None
        try:
            guard = request.getfixturevalue("app")
            widget = guard.root
        except pytest.FixtureLookupError:
            pass
        _save_screenshot(request.node.nodeid, widget)


def _save_screenshot(nodeid: str, widget: tk.Misc | None = None) -> None:
    """捕获截图并保存为 PNG 文件。

    优先使用 widget 的屏幕坐标截取指定窗口区域；如无法获取则截取全屏。

    Args:
        nodeid: pytest 测试节点 ID，用于生成文件名。
        widget: 可选的 Tkinter widget；若提供则只截取该窗口区域。
    """
    try:
        from PIL import ImageGrab
    except ImportError:
        return

    screenshot_dir = os.path.join(os.getcwd(), "test-screenshots")
    os.makedirs(screenshot_dir, exist_ok=True)

    # 将测试节点 ID 转换为合法文件名（替换路径分隔符和特殊字符）
    safe_name = nodeid.replace("/", "_").replace("::", "__").replace(" ", "_")
    path = os.path.join(screenshot_dir, f"{safe_name}.png")

    bbox = None
    if widget is not None:
        try:
            widget.update_idletasks()
            x = widget.winfo_rootx()
            y = widget.winfo_rooty()
            w = widget.winfo_width()
            h = widget.winfo_height()
            if w > 0 and h > 0:
                bbox = (x, y, x + w, y + h)
        except tk.TclError:
            pass

    try:
        img = ImageGrab.grab(bbox=bbox)
        img.save(path)
    except Exception:  # noqa: BLE001 – ImageGrab may raise OSError, RuntimeError, or
        pass           # platform-specific errors on headless runners; always ignore


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """将每个测试阶段的报告存储在 item 上，供 screenshot_on_failure fixture 使用。"""
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)
