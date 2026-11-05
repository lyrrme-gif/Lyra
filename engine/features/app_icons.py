"""Windows HICON -> transparent PNG data URL.

Drawing an icon onto a normal GDI bitmap throws its alpha channel away (every pixel comes
back with alpha 0), which is why app icons showed up as empty space. Here the icon is drawn
twice, on black and on white, and the true alpha is recovered from the difference.
"""
from __future__ import annotations

import base64
import ctypes
import io
from ctypes import wintypes


def hicon_to_data_url(hicon, size: int = 48) -> str | None:
    try:
        import numpy as np
        from PIL import Image

        user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
        HANDLE = ctypes.c_void_p
        user32.GetDC.argtypes = [HANDLE]; user32.GetDC.restype = HANDLE
        user32.ReleaseDC.argtypes = [HANDLE, HANDLE]
        user32.FillRect.argtypes = [HANDLE, ctypes.POINTER(wintypes.RECT), HANDLE]
        user32.DrawIconEx.argtypes = [HANDLE, ctypes.c_int, ctypes.c_int, HANDLE, ctypes.c_int,
                                      ctypes.c_int, wintypes.UINT, HANDLE, wintypes.UINT]
        gdi32.CreateCompatibleDC.argtypes = [HANDLE]; gdi32.CreateCompatibleDC.restype = HANDLE
        gdi32.CreateCompatibleBitmap.argtypes = [HANDLE, ctypes.c_int, ctypes.c_int]
        gdi32.CreateCompatibleBitmap.restype = HANDLE
        gdi32.SelectObject.argtypes = [HANDLE, HANDLE]; gdi32.SelectObject.restype = HANDLE
        gdi32.CreateSolidBrush.argtypes = [wintypes.COLORREF]; gdi32.CreateSolidBrush.restype = HANDLE
        gdi32.DeleteObject.argtypes = [HANDLE]
        gdi32.DeleteDC.argtypes = [HANDLE]
        gdi32.GetDIBits.argtypes = [HANDLE, HANDLE, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                                    ctypes.c_void_p, wintypes.UINT]

        class BIH(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                        ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                        ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                        ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

        class BMI(ctypes.Structure):
            _fields_ = [("bmiHeader", BIH), ("bmiColors", wintypes.DWORD * 3)]

        def render(color: int) -> "np.ndarray":
            hdc = user32.GetDC(None)
            mem = gdi32.CreateCompatibleDC(hdc)
            bmp = gdi32.CreateCompatibleBitmap(hdc, size, size)
            old = gdi32.SelectObject(mem, bmp)
            brush = gdi32.CreateSolidBrush(color)
            user32.FillRect(mem, ctypes.byref(wintypes.RECT(0, 0, size, size)), brush)
            gdi32.DeleteObject(brush)
            user32.DrawIconEx(mem, 0, 0, hicon, size, size, 0, None, 0x0003)  # DI_NORMAL
            bmi = BMI()
            bmi.bmiHeader.biSize = ctypes.sizeof(BIH)
            bmi.bmiHeader.biWidth, bmi.bmiHeader.biHeight = size, -size  # top-down
            bmi.bmiHeader.biPlanes, bmi.bmiHeader.biBitCount = 1, 32
            buf = (ctypes.c_ubyte * (size * size * 4))()
            gdi32.GetDIBits(mem, bmp, 0, size, buf, ctypes.byref(bmi), 0)
            gdi32.SelectObject(mem, old)
            gdi32.DeleteObject(bmp)
            gdi32.DeleteDC(mem)
            user32.ReleaseDC(None, hdc)
            return np.frombuffer(bytes(buf), dtype=np.uint8).reshape(size, size, 4)[:, :, :3].astype(np.float32)

        on_black = render(0x000000)
        on_white = render(0xFFFFFF)
        alpha = 1.0 - (on_white - on_black).mean(axis=2) / 255.0       # 0..1
        alpha = np.clip(alpha, 0.0, 1.0)
        safe = np.maximum(alpha, 1e-3)[:, :, None]
        rgb = np.clip(on_black / safe, 0, 255)                          # un-premultiply (BGR order)
        out = np.dstack([rgb[:, :, 2], rgb[:, :, 1], rgb[:, :, 0], alpha * 255.0]).astype(np.uint8)
        if int(out[:, :, 3].max()) == 0:
            return None
        buf_png = io.BytesIO()
        Image.fromarray(out, "RGBA").save(buf_png, format="PNG", optimize=True)
        return "data:image/png;base64," + base64.b64encode(buf_png.getvalue()).decode("ascii")
    except Exception:
        return None


def icon_for_target(target: str, kind: str = "file", size: int = 48) -> str | None:
    """Icon of an installed app: a normal file/shortcut path, or a Store (UWP) app id such as
    '5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App' (found through the shell's AppsFolder)."""
    try:
        import os
        shell32, user32, ole32 = ctypes.windll.shell32, ctypes.windll.user32, ctypes.windll.ole32

        class SHFILEINFOW(ctypes.Structure):
            _fields_ = [("hIcon", wintypes.HANDLE), ("iIcon", ctypes.c_int), ("dwAttributes", wintypes.DWORD),
                        ("szDisplayName", wintypes.WCHAR * 260), ("szTypeName", wintypes.WCHAR * 80)]

        fn = shell32.SHGetFileInfoW
        fn.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(SHFILEINFOW), wintypes.UINT, wintypes.UINT]
        fn.restype = ctypes.c_void_p
        info = SHFILEINFOW()
        SHGFI_ICON, SHGFI_PIDL = 0x100, 0x8
        if kind == "uwp":
            shell32.SHParseDisplayName.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p,
                                                   ctypes.POINTER(ctypes.c_void_p), wintypes.ULONG,
                                                   ctypes.POINTER(wintypes.ULONG)]
            ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
            pidl = ctypes.c_void_p()
            if shell32.SHParseDisplayName("shell:AppsFolder\\" + target, None, ctypes.byref(pidl), 0, None) != 0 \
                    or not pidl.value:
                return None
            try:
                fn(pidl, 0, ctypes.byref(info), ctypes.sizeof(info), SHGFI_PIDL | SHGFI_ICON)
            finally:
                ole32.CoTaskMemFree(pidl)
        else:
            path = os.path.expandvars(os.path.expanduser(target))
            if not os.path.exists(path):
                return None
            buf = ctypes.c_wchar_p(path)
            fn(ctypes.cast(buf, ctypes.c_void_p), 0, ctypes.byref(info), ctypes.sizeof(info), SHGFI_ICON)
        if not info.hIcon:
            return None
        try:
            return hicon_to_data_url(info.hIcon, size)
        finally:
            user32.DestroyIcon(info.hIcon)
    except Exception:
        return None
