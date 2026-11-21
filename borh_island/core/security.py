"""Windows DPAPI secret protection via ctypes (no external dependencies)."""

from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes as wt
import sys


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a UTF-8 secret with Windows DPAPI for the current user."""
    if not plaintext:
        return ""
    if sys.platform != "win32":
        return "plain:" + base64.b64encode(plaintext.encode("utf-8")).decode("ascii")

    raw = plaintext.encode("utf-8")
    buf = (ctypes.c_byte * len(raw))(*raw)
    blob_in = _DATA_BLOB(len(raw), buf)
    blob_out = _DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    if not crypt32.CryptProtectData(
        ctypes.byref(blob_in),
        "BorhIslandSecret",
        None,
        None,
        None,
        0,
        ctypes.byref(blob_out),
    ):
        raise OSError("CryptProtectData failed")

    try:
        encrypted = bytes(
            ctypes.cast(blob_out.pbData, ctypes.POINTER(ctypes.c_ubyte * blob_out.cbData)).contents
        )
    finally:
        kernel32.LocalFree(blob_out.pbData)
    return "dpapi:" + base64.b64encode(encrypted).decode("ascii")


def decrypt_secret(token: str) -> str:
    """Decrypt a DPAPI-protected secret string."""
    if not token:
        return ""
    if token.startswith("plain:"):
        return base64.b64decode(token[6:]).decode("utf-8", errors="replace")
    if not token.startswith("dpapi:"):
        return token
    if sys.platform != "win32":
        return ""

    raw = base64.b64decode(token[6:])
    if not raw:
        return ""
    buf = (ctypes.c_byte * len(raw))(*raw)
    blob_in = _DATA_BLOB(len(raw), buf)
    blob_out = _DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    if not crypt32.CryptUnprotectData(
        ctypes.byref(blob_in),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(blob_out),
    ):
        return ""

    try:
        decrypted = bytes(
            ctypes.cast(blob_out.pbData, ctypes.POINTER(ctypes.c_ubyte * blob_out.cbData)).contents
        )
    finally:
        kernel32.LocalFree(blob_out.pbData)
    return decrypted.decode("utf-8", errors="replace")


def mask_secret(secret: str) -> str:
    """Return a safe display mask for an API key."""
    if not secret:
        return ""
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:4]}...{secret[-4:]}"
