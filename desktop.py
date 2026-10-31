from __future__ import annotations

import ctypes
import os
import socket
import sys
import threading
import time
from pathlib import Path


def application_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_DIR = application_dir()
CONFIG_DIR = APP_DIR
if getattr(sys, "frozen", False) and not (CONFIG_DIR / ".env").exists():
    project_candidate = APP_DIR.parent.parent
    if (project_candidate / ".env").exists():
        CONFIG_DIR = project_candidate
os.chdir(CONFIG_DIR)
if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(APP_DIR / "src"))

import uvicorn
import webview

from pvz_agent_plugin.service import app


_mutex_handle: int | None = None


def acquire_single_instance() -> bool:
    """Prevent two desktop shells from controlling the same game at once."""
    global _mutex_handle
    if os.name != "nt":
        return True
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel32.CreateMutexW(None, False, "Local\\PVZ_AI_Studio_Desktop")
    if not handle:
        return False
    _mutex_handle = handle
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        ctypes.windll.user32.MessageBoxW(
            None, "PVZ AI Studio 已经在运行。", "PVZ AI Studio", 0x40
        )
        return False
    return True


def reserve_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def main() -> int:
    if not acquire_single_instance():
        return 1

    port = reserve_local_port()
    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="warning", access_log=False
    )
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None
    server_thread = threading.Thread(
        target=server.run, name="pvz-desktop-backend", daemon=True
    )
    server_thread.start()

    deadline = time.monotonic() + 10.0
    while not server.started and server_thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.05)
    if not server.started:
        ctypes.windll.user32.MessageBoxW(
            None, "桌面后台启动失败，请查看 logs 目录。", "PVZ AI Studio", 0x10
        )
        return 2

    webview.create_window(
        "PVZ AI Studio",
        f"http://127.0.0.1:{port}/?desktop=1&ui=10",
        width=1180,
        height=720,
        min_size=(960, 620),
        resizable=True,
        background_color="#090d12",
    )
    try:
        webview.start(gui="edgechromium", debug=False, private_mode=False)
    finally:
        server.should_exit = True
        server_thread.join(timeout=5.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

