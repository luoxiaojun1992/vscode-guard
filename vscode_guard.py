"""
VSCode 守护者 - 当有人靠近时自动切换到 VSCode 窗口
依赖: pip install opencv-python pillow pywin32
"""

import io
import math
import struct
import wave
import tkinter as tk
import cv2
import threading
import time
import winsound
from PIL import Image, ImageTk
import win32gui
import win32con
import win32api


# ──────────────────────────────────────────────
# 核心逻辑
# ──────────────────────────────────────────────
class VSCodeGuard:
    HISTORY_SIZE = 8        # 用于判断靠近趋势的帧数
    DETECT_INTERVAL = 3     # 每隔 N 帧做一次检测（降低 CPU）

    def __init__(self):
        self.active = False
        self.cap = None
        self.detect_thread = None
        self.current_frame = None
        self.frame_lock = threading.Lock()
        self._last_rects: list = []

        # 人脸检测器（OpenCV 内置 Haar 级联，适合坐在电脑前的近距离检测）
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        self.face_cascade = cv2.CascadeClassifier(cascade_path)

        # 靠近检测状态
        self.baseline_count: int | None = None  # 校准后的基准人脸数（通常=1，即用户自己）
        self.baseline_accum: list[int] = []      # 校准期采集的帧数据
        self.excess_streak: int = 0              # 连续超基准帧计数
        self.last_switch_time: float = 0.0
        self.switch_count: int = 0
        self._alarm_active: bool = False  # 当前入侵事件是否已报过警

        self._build_ui()

    # ── UI 构建 ────────────────────────────────
    def _build_ui(self):
        self.root = tk.Tk()
        self.root.title("VSCode 守护者")
        self.root.geometry("740x620")
        self.root.configure(bg="#1e1e2e")
        self.root.resizable(False, False)

        # 标题栏
        hdr = tk.Frame(self.root, bg="#181825", pady=10)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="VSCode 守护者", font=("Segoe UI", 15, "bold"),
                 fg="#cdd6f4", bg="#181825").pack()
        tk.Label(hdr, text="当检测到有人靠近时，自动切换到 VSCode 窗口",
                 font=("Segoe UI", 9), fg="#6c7086", bg="#181825").pack()

        # 主区域
        body = tk.Frame(self.root, bg="#1e1e2e")
        body.pack(fill=tk.BOTH, expand=True, padx=12, pady=10)

        # ── 左侧：摄像头预览 ──
        left = tk.Frame(body, bg="#1e1e2e")
        left.pack(side=tk.LEFT)

        self.canvas = tk.Canvas(left, width=480, height=360, bg="#11111b",
                                highlightthickness=1, highlightbackground="#313244")
        self.canvas.pack()
        self._canvas_placeholder()

        # ── 右侧：控制面板 ──
        right = tk.Frame(body, bg="#1e1e2e", width=210, padx=10)
        right.pack(side=tk.RIGHT, fill=tk.Y)
        right.pack_propagate(False)

        # 状态卡片
        self._card_status(right)

        # 开关按钮
        self.toggle_btn = tk.Button(
            right, text="▶  启动监控",
            font=("Segoe UI", 11, "bold"),
            fg="#1e1e2e", bg="#a6e3a1",
            activeforeground="#1e1e2e", activebackground="#94e2d5",
            relief=tk.FLAT, cursor="hand2", bd=0, pady=10,
            command=self.toggle
        )
        self.toggle_btn.pack(fill=tk.X, pady=(0, 4))

        # 切换次数
        self.switch_var = tk.StringVar(value="已切换: 0 次")
        tk.Label(right, textvariable=self.switch_var,
                 font=("Segoe UI", 8), fg="#585b70", bg="#1e1e2e").pack(pady=(0, 6))

        # 设置卡片
        self._card_settings(right)

        # 底部日志栏
        foot = tk.Frame(self.root, bg="#11111b", pady=5)
        foot.pack(fill=tk.X, side=tk.BOTTOM)
        self.log_var = tk.StringVar(value="就绪，点击「启动监控」开始")
        tk.Label(foot, textvariable=self.log_var,
                 font=("Consolas", 8), fg="#585b70", bg="#11111b").pack()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._refresh_canvas()

    def _card_status(self, parent):
        card = tk.Frame(parent, bg="#313244", padx=10, pady=10)
        card.pack(fill=tk.X, pady=(0, 8))

        # 状态行
        row = tk.Frame(card, bg="#313244")
        row.pack(fill=tk.X)
        tk.Label(row, text="状态", font=("Segoe UI", 8),
                 fg="#a6adc8", bg="#313244").pack(side=tk.LEFT)
        self.dot_canvas = tk.Canvas(row, width=10, height=10,
                                    bg="#313244", highlightthickness=0)
        self.dot_canvas.pack(side=tk.RIGHT, pady=2)
        self._dot = self.dot_canvas.create_oval(1, 1, 9, 9, fill="#585b70", outline="")

        self.status_var = tk.StringVar(value="未运行")
        self.status_lbl = tk.Label(card, textvariable=self.status_var,
                                   font=("Segoe UI", 12, "bold"),
                                   fg="#585b70", bg="#313244")
        self.status_lbl.pack(anchor=tk.W)

        tk.Frame(card, bg="#45475a", height=1).pack(fill=tk.X, pady=6)

        # 人数
        tk.Label(card, text="画面中的人脸", font=("Segoe UI", 8),
                 fg="#a6adc8", bg="#313244").pack(anchor=tk.W)
        self.count_var = tk.StringVar(value="0")
        tk.Label(card, textvariable=self.count_var,
                 font=("Segoe UI", 22, "bold"), fg="#cdd6f4", bg="#313244").pack(anchor=tk.W)

        # 靠近状态
        tk.Label(card, text="靠近判断", font=("Segoe UI", 8),
                 fg="#a6adc8", bg="#313244").pack(anchor=tk.W, pady=(6, 0))
        self.approach_var = tk.StringVar(value="—")
        self.approach_lbl = tk.Label(card, textvariable=self.approach_var,
                                     font=("Segoe UI", 10, "bold"),
                                     fg="#585b70", bg="#313244")
        self.approach_lbl.pack(anchor=tk.W)

    def _card_settings(self, parent):
        card = tk.Frame(parent, bg="#313244", padx=10, pady=10)
        card.pack(fill=tk.X, pady=(0, 8))

        tk.Label(card, text="⚙  设置", font=("Segoe UI", 9, "bold"),
                 fg="#cdd6f4", bg="#313244").pack(anchor=tk.W, pady=(0, 6))

        tk.Label(card, text="触发连续帧数（越大越稳定）", font=("Segoe UI", 8),
                 fg="#a6adc8", bg="#313244").pack(anchor=tk.W)
        self.sensitivity_var = tk.IntVar(value=3)
        tk.Scale(card, from_=1, to=8, resolution=1,
                 orient=tk.HORIZONTAL, variable=self.sensitivity_var,
                 bg="#313244", fg="#cdd6f4", troughcolor="#45475a",
                 highlightthickness=0, sliderlength=14, length=170,
                 showvalue=True, font=("Segoe UI", 7)).pack(fill=tk.X)

        tk.Label(card, text="切换冷却时间（秒）", font=("Segoe UI", 8),
                 fg="#a6adc8", bg="#313244").pack(anchor=tk.W, pady=(6, 0))
        self.cooldown_var = tk.IntVar(value=5)
        tk.Scale(card, from_=1, to=30, orient=tk.HORIZONTAL,
                 variable=self.cooldown_var,
                 bg="#313244", fg="#cdd6f4", troughcolor="#45475a",
                 highlightthickness=0, sliderlength=14, length=170,
                 showvalue=True, font=("Segoe UI", 7)).pack(fill=tk.X)

        tk.Button(card, text="↺  重新校准",
                  font=("Segoe UI", 8), fg="#cdd6f4", bg="#45475a",
                  activeforeground="#cdd6f4", activebackground="#585b70",
                  relief=tk.FLAT, cursor="hand2", bd=0, pady=4,
                  command=self._recalibrate).pack(fill=tk.X, pady=(8, 0))

    def _canvas_placeholder(self):
        self.canvas.delete("all")
        self.canvas.create_text(240, 180, text="摄像头未启动",
                                fill="#45475a", font=("Segoe UI", 13),
                                tags="placeholder")

    # ── 摄像头刷新（UI 线程定时器）──────────────
    def _refresh_canvas(self):
        with self.frame_lock:
            frame = self.current_frame
        if frame is not None:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(rgb).resize((480, 360), Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            self.canvas.delete("all")
            self.canvas.create_image(0, 0, anchor=tk.NW, image=photo)
            self.canvas.photo = photo  # 防止 GC
        self.root.after(66, self._refresh_canvas)  # ~15 fps

    # ── 开关 ───────────────────────────────────
    def toggle(self):
        if not self.active:
            self._start()
        else:
            self._stop()

    def _start(self):
        self.cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
        if not self.cap.isOpened():
            self._log("错误: 无法打开摄像头，请检查设备")
            return
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self.cap.set(cv2.CAP_PROP_FPS, 30)

        self.active = True
        self.baseline_count = None
        self.baseline_accum = []
        self.excess_streak = 0
        self._alarm_active = False
        self._last_rects = []

        self.detect_thread = threading.Thread(target=self._detection_loop, daemon=True)
        self.detect_thread.start()

        self.toggle_btn.config(text="⏹  停止监控", bg="#f38ba8",
                               activebackground="#eba0ac")
        self._set_status("校准中...", "#fab387", "#fab387")
        self._log("监控已启动，正在校准基准人脸数...")

    def _stop(self):
        self.active = False
        if self.cap:
            self.cap.release()
            self.cap = None
        with self.frame_lock:
            self.current_frame = None
        self._canvas_placeholder()

        self.toggle_btn.config(text="▶  启动监控", bg="#a6e3a1",
                               activebackground="#94e2d5")
        self._set_status("未运行", "#585b70", "#585b70")
        self.count_var.set("0")
        self.approach_var.set("—")
        self.approach_lbl.config(fg="#585b70")
        self._log("监控已停止")

    # ── 检测主循环（后台线程）──────────────────
    def _detection_loop(self):
        frame_idx = 0
        while self.active:
            if self.cap is None:
                break
            ret, frame = self.cap.read()
            if not ret:
                time.sleep(0.05)
                continue

            frame_idx += 1

            # 每 DETECT_INTERVAL 帧做一次行人检测
            if frame_idx % self.DETECT_INTERVAL == 0:
                self._process_frame(frame)

            # 绘制检测框
            display = frame.copy()
            for (x, y, w, h) in self._last_rects:
                cv2.rectangle(display, (x, y), (x + w, y + h), (166, 227, 161), 2)
                cv2.putText(display, "Face", (x, max(y - 6, 12)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (166, 227, 161), 1,
                            cv2.LINE_AA)

            with self.frame_lock:
                self.current_frame = display

            time.sleep(0.033)  # ~30 fps

    def _process_frame(self, frame: "cv2.Mat"):
        # 转灰度图加速人脸检测
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)  # 直方图均衡，改善弱光效果
        faces = self.face_cascade.detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=4,
            minSize=(60, 60), flags=cv2.CASCADE_SCALE_IMAGE
        )

        self._last_rects = list(faces) if len(faces) > 0 else []
        count = len(self._last_rects)
        self.root.after(0, lambda c=count: self.count_var.set(str(c)))

        # ── 校准阶段：收集基准人脸数（前 20 帧）──
        CALIBRATION_TOTAL = 20
        if self.baseline_count is None:
            self.baseline_accum.append(count)
            progress = len(self.baseline_accum)
            self.root.after(0, lambda p=progress, t=CALIBRATION_TOTAL:
                self._set_status(f"校准中 {p}/{t}", "#fab387", "#fab387"))
            if len(self.baseline_accum) >= CALIBRATION_TOTAL:
                self.baseline_count = round(
                    sum(self.baseline_accum) / len(self.baseline_accum)
                )
                bc = self.baseline_count
                self.root.after(0, lambda: self._log(f"校准完成，基准人脸数: {bc}，开始监控陌生人"))
                self.root.after(0, lambda: self._set_status("监控中", "#a6e3a1", "#a6e3a1"))
                self.root.after(0, lambda: self._update_approach_ui(False))
            return

        # ── 检测阶段：画面人脸数超过基准 = 有陌生人出现 ──
        extra = count - self.baseline_count
        if extra > 0:
            self.excess_streak += 1
        else:
            self.excess_streak = 0
            self._alarm_active = False  # 人离开后重置，下次可再次报警

        threshold = int(self.sensitivity_var.get())
        is_approaching = self.excess_streak >= threshold

        # 首次触发时播放低声报警（后台线程，不阻塞检测）
        if is_approaching and not self._alarm_active:
            self._alarm_active = True
            threading.Thread(target=self._play_alarm, daemon=True).start()

        self.root.after(0, lambda a=is_approaching: self._update_approach_ui(a))
        if is_approaching:
            self._maybe_switch_vscode()

    @staticmethod
    def _build_chime_wav() -> bytes:
        """用纯 Python 标准库生成柔和叮咚音（类 Windows 测试音）的 WAV 字节"""
        sample_rate = 44100
        volume = 0.12          # 12% 振幅，轻柔不刺耳
        # 两音：E5(659 Hz) → C5(523 Hz)，各 0.35 秒，正弦包络淡入淡出
        notes = [(659, 0.35), (523, 0.35)]
        samples: list[int] = []
        for freq, dur in notes:
            n = int(sample_rate * dur)
            for i in range(n):
                envelope = math.sin(math.pi * i / n)   # 0→1→0 平滑包络
                val = envelope * volume * math.sin(2 * math.pi * freq * i / sample_rate)
                samples.append(int(val * 32767))
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(struct.pack(f"<{len(samples)}h", *samples))
        return buf.getvalue()

    def _play_alarm(self):
        """播放类 Windows 测试音的柔和叮咚声"""
        try:
            wav = self._build_chime_wav()
            winsound.PlaySound(wav, winsound.SND_MEMORY)
        except Exception:
            pass  # 无声卡时静默忽略

    def _recalibrate(self):
        """重置校准，下次检测时重新采集基准人脸数"""
        if self.active:
            self.baseline_count = None
            self.baseline_accum = []
            self.excess_streak = 0
            self._set_status("重新校准...", "#fab387", "#fab387")
            self._update_approach_ui(None)
            self._log("已重置校准，正在重新采集基准...")

    # ── UI 更新助手 ────────────────────────────
    def _update_approach_ui(self, approaching):
        if approaching is None:
            self.approach_var.set("—")
            self.approach_lbl.config(fg="#585b70")
            self._set_status("监控中", "#a6e3a1", "#a6e3a1")
        elif approaching:
            self.approach_var.set("⚠  有人靠近！")
            self.approach_lbl.config(fg="#f38ba8")
            self._set_status("警报！", "#f38ba8", "#f38ba8")
        else:
            self.approach_var.set("✓ 无人靠近")
            self.approach_lbl.config(fg="#89b4fa")
            self._set_status("监控中", "#a6e3a1", "#a6e3a1")

    def _set_status(self, text: str, text_color: str, dot_color: str):
        self.status_var.set(text)
        self.status_lbl.config(fg=text_color)
        self.dot_canvas.itemconfig(self._dot, fill=dot_color)

    # ── VSCode 窗口切换 ──────────────────────────
    def _maybe_switch_vscode(self):
        now = time.time()
        if now - self.last_switch_time < self.cooldown_var.get():
            return
        hwnd = self._find_vscode_hwnd()
        if hwnd:
            self.last_switch_time = now
            self.switch_count += 1
            cnt = self.switch_count
            self.root.after(0, lambda: self.switch_var.set(f"已切换: {cnt} 次"))
            self.root.after(0, lambda: self._log(f"检测到有人靠近，已切换到 VSCode"))
            self._bring_to_front(hwnd)
        else:
            self.root.after(0, lambda: self._log("未找到 VSCode 窗口"))

    def _find_vscode_hwnd(self) -> int | None:
        found = []

        def _cb(hwnd, _):
            if win32gui.IsWindowVisible(hwnd) and win32gui.IsWindowEnabled(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if "Visual Studio Code" in title:
                    found.append(hwnd)

        win32gui.EnumWindows(_cb, None)
        return found[0] if found else None

    @staticmethod
    def _bring_to_front(hwnd: int):
        """将指定窗口提到前台（处理 Windows 焦点限制）"""
        try:
            # 模拟 Alt 键解除系统前台锁定
            win32api.keybd_event(0x12, 0, 0, 0)           # Alt 按下
            placement = win32gui.GetWindowPlacement(hwnd)
            if placement[1] == win32con.SW_SHOWMINIMIZED:
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
            win32api.keybd_event(0x12, 0, win32con.KEYEVENTF_KEYUP, 0)  # Alt 释放
        except Exception:
            # 备用方案：发送激活消息
            try:
                win32gui.SendMessage(hwnd, win32con.WM_ACTIVATE,
                                     win32con.WA_CLICKACTIVE, 0)
                win32gui.SetForegroundWindow(hwnd)
            except Exception:
                pass

    # ── 日志 / 关闭 ───────────────────────────
    def _log(self, msg: str):
        self.log_var.set(f"[{time.strftime('%H:%M:%S')}]  {msg}")

    def _on_close(self):
        self._stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


# ──────────────────────────────────────────────
if __name__ == "__main__":
    app = VSCodeGuard()
    app.run()
