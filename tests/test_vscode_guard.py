"""
VSCode 守护者 UI 测试。

摄像头模拟方案
--------------
在 CI 环境中没有真实摄像头，使用 tests.fake_camera.FakeCapture：
  - 实现与 cv2.VideoCapture 相同的接口
  - 返回合成 BGR numpy 帧，不依赖任何硬件
  - 通过 patch cv2.VideoCapture 注入，测试代码无需修改

人脸检测控制方案
----------------
cv2.CascadeClassifier.detectMultiScale 是 C 扩展只读属性，无法直接 patch。
用 MagicMock 替换整个 app.face_cascade 对象，通过
  mock_cascade.detectMultiScale.return_value = ...
精确控制每帧检测结果，覆盖"无人脸"/"多人脸"/"陌生人出现"等场景。

需要检测结果更新 UI 的测试直接从主线程调用 _process_frame()，
避免后台检测线程与 Tkinter 事件循环之间的跨线程限制。
"""

import time
from unittest.mock import patch, MagicMock

import numpy as np
import pytest

from tests.conftest import pump
from tests.fake_camera import FakeCapture


# ── 辅助数据 ──────────────────────────────────────────────────────────────────

NO_FACES = np.empty((0, 4), dtype=np.int32)
ONE_FACE = np.array([[50, 50, 80, 80]], dtype=np.int32)
TWO_FACES = np.array([[50, 50, 80, 80], [200, 100, 80, 80]], dtype=np.int32)


# ── 初始状态测试 ──────────────────────────────────────────────────────────────

class TestInitialState:
    """应用启动后、点击任何按钮前的默认 UI 状态。"""

    def test_window_title(self, app):
        assert app.root.title() == "VSCode 守护者"

    def test_not_active(self, app):
        assert app.active is False

    def test_status_text(self, app):
        assert app.status_var.get() == "未运行"

    def test_face_count_zero(self, app):
        assert app.count_var.get() == "0"

    def test_approach_dash(self, app):
        assert app.approach_var.get() == "—"

    def test_button_text_start(self, app):
        assert "启动监控" in app.toggle_btn.cget("text")

    def test_switch_count_zero(self, app):
        assert "0" in app.switch_var.get()

    def test_sensitivity_default(self, app):
        assert app.sensitivity_var.get() == 3

    def test_cooldown_default(self, app):
        assert app.cooldown_var.get() == 5

    def test_window_visible(self, app):
        app.root.update()
        assert app.root.winfo_viewable()

    def test_log_contains_hint(self, app):
        # 初始日志应提示用户点击按钮启动
        assert "就绪" in app.log_var.get()
        assert "启动监控" in app.log_var.get()


# ── 启动 / 停止监控测试 ────────────────────────────────────────────────────────

class TestToggle:
    """模拟点击启动/停止按钮，验证状态转换。"""

    def test_start_with_fake_camera(self, app):
        with patch("cv2.VideoCapture", return_value=FakeCapture()):
            app.toggle()
            pump(app.root)
            assert app.active is True
            app._stop()

    def test_camera_unavailable_stays_inactive(self, app):
        """摄像头打开失败时，应用不应进入 active 状态。"""
        with patch("cv2.VideoCapture", return_value=FakeCapture(opened=False)):
            app.toggle()
            pump(app.root)
            assert app.active is False

    def test_button_text_changes_to_stop(self, app):
        with patch("cv2.VideoCapture", return_value=FakeCapture()):
            app.toggle()
            pump(app.root)
            assert "停止监控" in app.toggle_btn.cget("text")
            app._stop()

    def test_button_text_reverts_to_start(self, app):
        with patch("cv2.VideoCapture", return_value=FakeCapture()):
            app.toggle()
            pump(app.root)
            app.toggle()
            pump(app.root)
            assert "启动监控" in app.toggle_btn.cget("text")

    def test_status_changes_on_start(self, app):
        """启动后状态不应再显示"未运行"（变为"校准中..."）。"""
        with patch("cv2.VideoCapture", return_value=FakeCapture()):
            app.toggle()
            pump(app.root)
            assert app.status_var.get() != "未运行"
            app._stop()

    def test_status_reverts_on_stop(self, app):
        with patch("cv2.VideoCapture", return_value=FakeCapture()):
            app.toggle()
            pump(app.root)
            app.toggle()
            pump(app.root)
            assert app.status_var.get() == "未运行"

    def test_cap_released_on_stop(self, app):
        with patch("cv2.VideoCapture", return_value=FakeCapture()):
            app.toggle()
            pump(app.root)
            app._stop()
            assert app.cap is None

    def test_log_updates_on_start(self, app):
        with patch("cv2.VideoCapture", return_value=FakeCapture()):
            app.toggle()
            pump(app.root, n=3, delay=0.05)
            # 启动后日志应已更新（不再是初始的就绪提示）
            assert app.log_var.get() != "就绪，点击「启动监控」开始"
            app._stop()

    def test_log_updates_on_stop(self, app):
        with patch("cv2.VideoCapture", return_value=FakeCapture()):
            app.toggle()
            pump(app.root)
            app.toggle()
            pump(app.root)
            assert "停止" in app.log_var.get()


# ── 合成摄像头 / 人脸检测测试 ─────────────────────────────────────────────────

class TestFakeCamera:
    """
    验证 FakeCapture 返回的合成帧可被检测循环消费，
    并通过 patch detectMultiScale 精确控制检测结果。
    """

    def test_fake_capture_frame_shape(self):
        fake = FakeCapture()
        ret, frame = fake.read()
        assert ret is True
        assert frame.shape == (480, 640, 3)
        assert frame.dtype == np.uint8

    def test_fake_capture_closed_returns_false(self):
        fake = FakeCapture(opened=False)
        ret, frame = fake.read()
        assert ret is False
        assert frame is None

    def test_fake_capture_release(self):
        fake = FakeCapture()
        fake.release()
        assert fake.isOpened() is False

    def test_face_count_no_faces(self, app):
        mock_cascade = MagicMock()
        mock_cascade.detectMultiScale.return_value = NO_FACES
        app.face_cascade = mock_cascade
        # Call directly from main thread to avoid Tkinter cross-thread restrictions
        frame = np.full((480, 640, 3), 100, dtype=np.uint8)
        app._process_frame(frame)
        pump(app.root, n=2, delay=0.01)
        assert app.count_var.get() == "0"

    def test_face_count_one_face(self, app):
        mock_cascade = MagicMock()
        mock_cascade.detectMultiScale.return_value = ONE_FACE
        app.face_cascade = mock_cascade
        frame = np.full((480, 640, 3), 100, dtype=np.uint8)
        app._process_frame(frame)
        pump(app.root, n=2, delay=0.01)
        assert int(app.count_var.get()) == 1

    def test_face_count_two_faces(self, app):
        mock_cascade = MagicMock()
        mock_cascade.detectMultiScale.return_value = TWO_FACES
        app.face_cascade = mock_cascade
        frame = np.full((480, 640, 3), 100, dtype=np.uint8)
        app._process_frame(frame)
        pump(app.root, n=2, delay=0.01)
        assert int(app.count_var.get()) == 2

    def test_calibration_completes(self, app):
        """
        连续调用 _process_frame 20+ 次后，baseline_count 应完成校准。
        直接从主线程调用，无需等待后台检测循环。
        """
        mock_cascade = MagicMock()
        mock_cascade.detectMultiScale.return_value = NO_FACES
        app.face_cascade = mock_cascade
        frame = np.full((480, 640, 3), 100, dtype=np.uint8)
        for _ in range(25):  # 超过校准所需的 20 帧
            app._process_frame(frame)
            pump(app.root, n=1, delay=0.01)
        assert app.baseline_count == 0  # 无人脸 → 基准为 0


# ── 靠近检测 UI 测试 ──────────────────────────────────────────────────────────

class TestApproachUI:
    """验证"靠近判断"区域的 UI 状态更新。"""

    def test_no_approach_label(self, app):
        app.root.after(0, lambda: app._update_approach_ui(False))
        pump(app.root)
        assert "无人靠近" in app.approach_var.get()

    def test_approach_detected_label(self, app):
        app.root.after(0, lambda: app._update_approach_ui(True))
        pump(app.root)
        assert "靠近" in app.approach_var.get()

    def test_approach_reset_label(self, app):
        app.root.after(0, lambda: app._update_approach_ui(None))
        pump(app.root)
        assert app.approach_var.get() == "—"

    def test_approach_triggers_switch(self, app):
        """
        将基准强制设为 0，注入 1 张人脸，灵敏度设为 1，
        验证 _maybe_switch_vscode 被调用（即触发了陌生人报警）。
        """
        mock_cascade = MagicMock()
        mock_cascade.detectMultiScale.return_value = ONE_FACE
        app.face_cascade = mock_cascade
        app.baseline_count = 0       # 跳过校准阶段
        app.sensitivity_var.set(1)   # 最灵敏：1 帧即触发
        frame = np.full((480, 640, 3), 100, dtype=np.uint8)
        with patch.object(app, "_maybe_switch_vscode") as mock_switch:
            app._process_frame(frame)
            pump(app.root, n=2, delay=0.01)
            assert mock_switch.called

    def test_no_approach_when_baseline_not_exceeded(self, app):
        """baseline=1，检测到 1 张脸 → 不超基准 → 不触发切换。"""
        mock_cascade = MagicMock()
        mock_cascade.detectMultiScale.return_value = ONE_FACE
        app.face_cascade = mock_cascade
        app.baseline_count = 1       # 基准=1（即用户本人）
        app.sensitivity_var.set(2)   # 需要连续 2 帧才触发
        frame = np.full((480, 640, 3), 100, dtype=np.uint8)
        with patch.object(app, "_maybe_switch_vscode") as mock_switch:
            # 只调用一次：excess_streak=1 < sensitivity=2，不触发
            app._process_frame(frame)
            pump(app.root, n=2, delay=0.01)
            assert not mock_switch.called


# ── 设置面板测试 ──────────────────────────────────────────────────────────────

class TestSettings:
    def test_set_sensitivity(self, app):
        app.sensitivity_var.set(7)
        assert app.sensitivity_var.get() == 7

    def test_set_cooldown(self, app):
        app.cooldown_var.set(20)
        assert app.cooldown_var.get() == 20

    def test_recalibrate_resets_baseline(self, app):
        with patch("cv2.VideoCapture", return_value=FakeCapture()):
            app.toggle()
            pump(app.root, n=3, delay=0.05)
            app.baseline_count = 1   # 假设已完成校准
            app._recalibrate()
            pump(app.root, n=2, delay=0.05)
            assert app.baseline_count is None
            app._stop()

    def test_recalibrate_while_inactive_no_crash(self, app):
        """非运行状态下点击重新校准不应报错。"""
        app._recalibrate()
        pump(app.root, n=2, delay=0.05)
        assert app.active is False

    def test_recalibrate_resets_streak(self, app):
        with patch("cv2.VideoCapture", return_value=FakeCapture()):
            app.toggle()
            pump(app.root, n=2, delay=0.05)
            app.excess_streak = 5
            app._recalibrate()
            pump(app.root, n=2, delay=0.05)
            assert app.excess_streak == 0
            app._stop()
