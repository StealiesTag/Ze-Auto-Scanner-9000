"""Server-side encryption for Canvas personal access tokens."""
from __future__ import annotations

import base64
import ctypes
import os
from ctypes import wintypes


class TokenEncryptionError(RuntimeError):
    pass


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes):
    buffer = ctypes.create_string_buffer(data)
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def _windows_dpapi(data: bytes, entropy: bytes, *, decrypt: bool) -> bytes:
    if os.name != "nt":
        raise TokenEncryptionError("Set CANVAS_TOKEN_ENCRYPTION_KEY and install the cryptography dependency on this server.")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [ctypes.POINTER(_DataBlob), wintypes.LPCWSTR, ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob)]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(_DataBlob), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob)]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    source, source_buffer = _blob(data)
    extra, extra_buffer = _blob(entropy)
    result = _DataBlob()
    if decrypt:
        ok = crypt32.CryptUnprotectData(ctypes.byref(source), None, ctypes.byref(extra), None, None, 0x1, ctypes.byref(result))
    else:
        ok = crypt32.CryptProtectData(ctypes.byref(source), "Paparazzi Canvas token", ctypes.byref(extra), None, None, 0x1, ctypes.byref(result))
    if not ok:
        raise TokenEncryptionError("Windows could not encrypt or decrypt the Canvas token for this account.")
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel32.LocalFree(result.pbData)
        del source_buffer, extra_buffer


def encrypt_access_token(token: str, config: dict) -> str:
    secret_key = (config.get("CANVAS_TOKEN_ENCRYPTION_KEY") or "").strip()
    if secret_key:
        try:
            from cryptography.fernet import Fernet
            return "fernet:" + Fernet(secret_key.encode("ascii")).encrypt(token.encode("utf-8")).decode("ascii")
        except Exception as error:
            raise TokenEncryptionError("Canvas token encryption is not configured correctly.") from error
    encrypted = _windows_dpapi(token.encode("utf-8"), str(config.get("SECRET_KEY", "")).encode("utf-8"), decrypt=False)
    return "dpapi:" + base64.urlsafe_b64encode(encrypted).decode("ascii")


def decrypt_access_token(stored: str, config: dict) -> str:
    kind, separator, payload = (stored or "").partition(":")
    if not separator:
        raise TokenEncryptionError("The stored Canvas token is not encrypted in a supported format.")
    try:
        if kind == "fernet":
            from cryptography.fernet import Fernet
            return Fernet((config.get("CANVAS_TOKEN_ENCRYPTION_KEY") or "").encode("ascii")).decrypt(payload.encode("ascii")).decode("utf-8")
        if kind == "dpapi":
            encrypted = base64.urlsafe_b64decode(payload.encode("ascii"))
            return _windows_dpapi(encrypted, str(config.get("SECRET_KEY", "")).encode("utf-8"), decrypt=True).decode("utf-8")
    except TokenEncryptionError:
        raise
    except Exception as error:
        raise TokenEncryptionError("The stored Canvas token cannot be decrypted with this server's key.") from error
    raise TokenEncryptionError("The stored Canvas token is not encrypted in a supported format.")
