"""Readiness must be measured in the actual background process, not its installer."""
from datetime import timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import sys
import tempfile

from .outbox import canonical
from .policy import instant, utcnow


def configuration_hash(config):
    helper = Path(__file__).parent / "native" / ("windows-capture.ps1" if platform.system() == "Windows" else "mac-capture")
    identity = sha256(helper.read_bytes()).hexdigest() if helper.is_file() else "helper_missing"
    return sha256(canonical({"config": {k: v for k, v in config.items() if k != "capture_enabled"},
                             "helper_sha256": identity, "runtime": os.path.realpath(sys.executable)})).hexdigest()


def background_context():
    if platform.system() == "Darwin":
        return os.getppid() == 1 and os.environ.get("COMPANY_PILOT_AGENT_ID") == "com.company-pilot.desktop"
    if platform.system() == "Windows":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x1000, False, os.getppid())
        if not handle:
            return False
        try:
            buffer, size = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
            if not kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return False
            return Path(buffer.value).name.casefold() in {"taskeng.exe", "taskhostw.exe", "svchost.exe"}
        finally:
            kernel.CloseHandle(handle)
    return False


def write_health(path, config, native_probe, *, context_verified=None, now=None):
    now = now or utcnow()
    body = {"schema_version": 1, "device_id": config["device_id"], "configuration_sha256": configuration_hash(config),
            "observed_at": now.isoformat(), "valid_until": (now + timedelta(seconds=90)).isoformat(),
            "pid": os.getpid(), "parent_pid": os.getppid(), "context": "background_agent",
            "context_verified": background_context() if context_verified is None else context_verified,
            "native_probe": native_probe, "capture_enabled": config.get("capture_enabled") is True}
    path = Path(path)
    descriptor, temporary = tempfile.mkstemp(prefix="health-", dir=path.parent)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(body, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    return body


def read_health(path, config, now=None):
    try:
        body = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(body, dict) or not isinstance(body.get("native_probe"), dict):
            raise ValueError("invalid_agent_health")
        if body["configuration_sha256"] != configuration_hash(config) or body.get("context_verified") is not True or instant(body["valid_until"]) <= (now or utcnow()) or not _alive(body["pid"]):
            return {"status": "agent_unhealthy_or_stale", "ready": False}
        return body
    except (OSError, KeyError, ValueError):
        return {"status": "agent_not_reporting", "ready": False}


def _alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    if platform.system() == "Windows":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle: return False
        try:
            code = wintypes.DWORD()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally: kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def native_ready(probe, images):
    ready = probe.get("accessibility_granted") is True or (probe.get("uia_available") is True and probe.get("interactive_session") is True)
    if probe.get("platform") == "macOS" and images:
        ready = ready and probe.get("screen_recording_granted") is True
    return ready
