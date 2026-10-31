from __future__ import annotations

import shutil
import subprocess
import threading
import time
from collections import deque
from collections.abc import Iterator


class GameCaptureHub:
    """Share one FFmpeg window capture between every local dashboard client."""

    def __init__(self, window_title: str, fps: int = 30, quality: int = 4) -> None:
        self.window_title = window_title
        self.fps = max(1, min(int(fps), 60))
        self.quality = max(2, min(int(quality), 12))
        self._condition = threading.Condition()
        self._thread: threading.Thread | None = None
        self._frame = b""
        self._version = 0
        self._last_frame_at = 0.0
        self._last_error = ""
        self._process: subprocess.Popen[bytes] | None = None
        self._stop = threading.Event()
        self._frame_times: deque[float] = deque(maxlen=max(10, self.fps * 2))
        self._width = 0
        self._height = 0
        self._clients = 0

    def ensure_started(self) -> None:
        with self._condition:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._capture_loop, name="pvz-window-capture", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        process = self._process
        if process and process.poll() is None:
            process.kill()
        with self._condition:
            self._condition.notify_all()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    def status(self) -> dict[str, object]:
        age = time.monotonic() - self._last_frame_at if self._last_frame_at else None
        frame_times = list(self._frame_times)
        measured_fps = 0.0
        if len(frame_times) >= 2 and frame_times[-1] > frame_times[0]:
            measured_fps = (len(frame_times) - 1) / (frame_times[-1] - frame_times[0])
        process = self._process
        return {
            "running": bool(self._thread and self._thread.is_alive()),
            "process_running": bool(process and process.poll() is None),
            "process_pid": process.pid if process and process.poll() is None else None,
            "window_title": self.window_title,
            "fps": self.fps,
            "measured_fps": round(measured_fps, 1),
            "frame_width": self._width,
            "frame_height": self._height,
            "clients": self._clients,
            "has_frame": bool(self._frame),
            "last_frame_age_ms": round(age * 1000) if age is not None else None,
            "last_error": self._last_error,
        }

    def stream(self) -> Iterator[bytes]:
        self.ensure_started()
        seen = -1
        with self._condition:
            self._clients += 1
        try:
            while not self._stop.is_set():
                with self._condition:
                    self._condition.wait_for(
                        lambda: self._version != seen or self._stop.is_set(), timeout=3.0
                    )
                    if self._stop.is_set():
                        return
                    if not self._frame:
                        continue
                    seen = self._version
                    frame = self._frame
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    + f"Content-Length: {len(frame)}\r\n\r\n".encode("ascii")
                    + frame
                    + b"\r\n"
                )
        finally:
            with self._condition:
                self._clients = max(0, self._clients - 1)

    def _capture_loop(self) -> None:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            self._set_error("未找到 FFmpeg，无法捕获游戏窗口")
            return
        while not self._stop.is_set():
            command = [
                ffmpeg, "-hide_banner", "-loglevel", "error",
                "-f", "gdigrab", "-draw_mouse", "0",
                "-framerate", str(self.fps),
                "-i", f"title={self.window_title}",
                "-an", "-c:v", "mjpeg", "-q:v", str(self.quality), "-threads", "1",
                "-f", "image2pipe", "pipe:1",
            ]
            try:
                flags = (
                    getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
                )
                with self._condition:
                    self._frame = b""
                    self._last_frame_at = 0.0
                    self._frame_times.clear()
                    self._width = self._height = 0
                    self._version += 1
                    self._condition.notify_all()
                self._process = subprocess.Popen(
                    command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    creationflags=flags, bufsize=0,
                )
                self._last_error = ""
                watchdog = threading.Thread(
                    target=self._watch_process, args=(self._process,),
                    name="pvz-capture-watchdog", daemon=True,
                )
                watchdog.start()
                self._read_frames(self._process)
                error = self._process.stderr.read().decode("utf-8", "replace").strip() if self._process.stderr else ""
                if not self._stop.is_set() and error:
                    self._set_error(error)
                elif not self._stop.is_set() and not self._last_error:
                    self._set_error("游戏窗口捕获已停止，正在重连")
            except Exception as exc:  # pragma: no cover - platform/runtime guard
                self._set_error(str(exc))
            finally:
                if self._process and self._process.poll() is None:
                    self._process.kill()
                self._process = None
            if not self._stop.wait(0.75):
                continue

    def _watch_process(self, process: subprocess.Popen[bytes]) -> None:
        started = time.monotonic()
        while not self._stop.wait(0.5):
            if process is not self._process or process.poll() is not None:
                return
            last_frame = self._last_frame_at
            if (not last_frame and time.monotonic() - started > 4.0) or (
                last_frame and time.monotonic() - last_frame > 2.5
            ):
                self._set_error("视频流超过 2.5 秒没有新帧，正在自动重连")
                process.kill()
                return

    def _read_frames(self, process: subprocess.Popen[bytes]) -> None:
        if process.stdout is None:
            return
        buffer = bytearray()
        while True:
            chunk = process.stdout.read(65536)
            if not chunk:
                return
            buffer.extend(chunk)
            while True:
                start = buffer.find(b"\xff\xd8")
                if start < 0:
                    if len(buffer) > 2:
                        del buffer[:-2]
                    break
                end = buffer.find(b"\xff\xd9", start + 2)
                if end < 0:
                    if start:
                        del buffer[:start]
                    break
                frame = bytes(buffer[start:end + 2])
                del buffer[:end + 2]
                width, height = self._jpeg_dimensions(frame)
                if width < 300 or height < 200:
                    self._set_error(f"捕获画面尺寸异常: {width}x{height}，正在重建视频流")
                    with self._condition:
                        self._frame = b""
                        self._version += 1
                        self._condition.notify_all()
                    if process.poll() is None:
                        process.kill()
                    return
                with self._condition:
                    self._frame = frame
                    self._version += 1
                    now = time.monotonic()
                    self._last_frame_at = now
                    self._frame_times.append(now)
                    self._width, self._height = width, height
                    self._condition.notify_all()

    @staticmethod
    def _jpeg_dimensions(frame: bytes) -> tuple[int, int]:
        """Read JPEG SOF dimensions without adding an image dependency."""
        index = 2
        while index + 8 < len(frame):
            if frame[index] != 0xFF:
                index += 1
                continue
            marker = frame[index + 1]
            if marker in {0xD8, 0xD9}:
                index += 2
                continue
            if index + 4 > len(frame):
                break
            length = int.from_bytes(frame[index + 2:index + 4], "big")
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                if index + 9 <= len(frame):
                    height = int.from_bytes(frame[index + 5:index + 7], "big")
                    width = int.from_bytes(frame[index + 7:index + 9], "big")
                    return width, height
                break
            if length < 2:
                break
            index += 2 + length
        return 0, 0

    def _set_error(self, message: str) -> None:
        with self._condition:
            self._last_error = message[-500:]
            self._condition.notify_all()

