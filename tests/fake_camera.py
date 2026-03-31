"""
合成摄像头 —— 在 CI 环境中代替真实摄像头返回 BGR 帧，无需硬件支持。
"""

import numpy as np


class FakeCapture:
    """
    模拟 cv2.VideoCapture 接口。
    返回均匀灰色合成 BGR 帧，让检测循环可以正常运行，
    同时通过 patch 控制 detectMultiScale 的输出来模拟人脸出现/消失。
    """

    def __init__(self, width: int = 640, height: int = 480, opened: bool = True):
        self._width = width
        self._height = height
        self._opened = opened
        self._frame_count = 0

    # ── cv2.VideoCapture 接口 ──────────────────────────────────────────────

    def isOpened(self) -> bool:
        return self._opened

    def read(self):
        if not self._opened:
            return False, None
        self._frame_count += 1
        # 均匀灰色背景，不会触发真实 Haar 人脸检测
        frame = np.full((self._height, self._width, 3), 100, dtype=np.uint8)
        return True, frame

    def set(self, prop_id: int, value) -> bool:
        return True  # 接受但忽略所有属性设置

    def release(self):
        self._opened = False
