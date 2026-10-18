from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
import webbrowser
from ctypes import wintypes
from typing import Any


def _focus_existing_yandex_window(executable: str) -> bool:
    if sys.platform != "win32":
        raise RuntimeError(
            "Reusing an existing browser window is only configured for Windows"
        )

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    process_query_limited_information = 0x1000
    sw_restore = 9
    gw_owner = 4
    target_executable = os.path.normcase(os.path.abspath(executable))

    enum_windows_proc = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
    )
    user32.EnumWindows.argtypes = [enum_windows_proc, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.BringWindowToTop.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    user32.AttachThreadInput.restype = wintypes.BOOL

    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD

    matching_windows: list[wintypes.HWND] = []

    @enum_windows_proc
    def collect_window(hwnd: wintypes.HWND, _lparam: wintypes.LPARAM) -> bool:
        if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, gw_owner):
            return True

        title_length = user32.GetWindowTextLengthW(hwnd)
        if not title_length:
            return True
        title = ctypes.create_unicode_buffer(title_length + 1)
        user32.GetWindowTextW(hwnd, title, len(title))
        normalized_title = " ".join(
            title.value.casefold().replace("\u00a0", " ").split()
        )
        if (
            "яндекс браузер" not in normalized_title
            and "yandex browser" not in normalized_title
        ):
            return True

        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        process_handle = kernel32.OpenProcess(
            process_query_limited_information, False, process_id.value
        )
        if not process_handle:
            return True

        try:
            image_path = ctypes.create_unicode_buffer(32768)
            image_path_length = wintypes.DWORD(len(image_path))
            if kernel32.QueryFullProcessImageNameW(
                process_handle, 0, image_path, ctypes.byref(image_path_length)
            ):
                actual_executable = os.path.normcase(os.path.abspath(image_path.value))
                if actual_executable == target_executable:
                    matching_windows.append(hwnd)
        finally:
            kernel32.CloseHandle(process_handle)
        return True

    user32.EnumWindows(collect_window, 0)
    if not matching_windows:
        return False

    hwnd = matching_windows[0]
    foreground = user32.GetForegroundWindow()
    foreground_thread = (
        user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
    )
    target_thread = user32.GetWindowThreadProcessId(hwnd, None)
    current_thread = kernel32.GetCurrentThreadId()
    attached_threads: list[int] = []
    for thread_id in {foreground_thread, target_thread}:
        if thread_id and thread_id != current_thread:
            if user32.AttachThreadInput(current_thread, thread_id, True):
                attached_threads.append(thread_id)

    try:
        user32.ShowWindow(hwnd, sw_restore)
        user32.BringWindowToTop(hwnd)
        activated = bool(user32.SetForegroundWindow(hwnd))
        if not activated and user32.GetForegroundWindow() != hwnd:
            raise OSError(
                ctypes.get_last_error(),
                "Windows did not activate the Yandex Browser window",
            )
    finally:
        for thread_id in attached_threads:
            user32.AttachThreadInput(current_thread, thread_id, False)

    return True


def execute(action: dict[str, Any], *, browser_executable: str | None = None) -> None:
    kind = action["type"]
    value = action["value"]
    if kind == "open_url":
        if browser_executable and sys.platform == "win32":
            subprocess.Popen([browser_executable, value], close_fds=True)
        elif not webbrowser.open(value, new=2):
            raise RuntimeError(f"Browser did not accept URL {value}")
        return
    if kind == "open_app":
        if sys.platform == "win32":
            if action.get("reuse_existing_window") and _focus_existing_yandex_window(
                value
            ):
                logging.getLogger("radu.v3").info(
                    "Activated the existing Yandex Browser window"
                )
                return
            if value.startswith("shell:AppsFolder\\"):
                os.startfile(value)
                return
            subprocess.Popen([value], close_fds=True)
            return
        raise RuntimeError("open_app action is only configured for Windows")
    raise ValueError(f"Unsupported action type {kind!r}")
