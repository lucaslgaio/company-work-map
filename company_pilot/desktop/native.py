"""Bounded subprocess adapter. No permission prompts, automation or whole-screen fallback."""
import base64
import json
import platform
from pathlib import Path
import subprocess

from .collector import NativeFailure


class NativeAdapter:
    def __init__(self, helper: Path | None = None, timeout: float = 8):
        system = platform.system()
        root = Path(__file__).parent / "native"
        if system == "Darwin":
            self.command = [str(helper or root / "mac-capture")]
        elif system == "Windows":
            self.command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-File", str(helper or root / "windows-capture.ps1"), "-Action"]
        else:
            raise NativeFailure("unsupported_platform")
        self.timeout = timeout

    def _call(self, action: str, expected: dict | None = None):
        try:
            result = subprocess.run(self.command + [action], input=json.dumps(expected or {}),
                                    text=True, encoding="utf-8", capture_output=True, timeout=self.timeout, check=False)
        except subprocess.TimeoutExpired:
            raise NativeFailure("native_helper_timeout") from None
        except OSError:
            raise NativeFailure("native_helper_missing") from None
        if len(result.stdout) > 10 * 1024 * 1024:
            raise NativeFailure("native_output_too_large")
        try:
            payload = json.loads(result.stdout)
        except (json.JSONDecodeError, ValueError):
            raise NativeFailure("native_helper_invalid_response") from None
        if not isinstance(payload, dict):
            raise NativeFailure("native_helper_invalid_response")
        if result.returncode != 0 or payload.get("error"):
            allowed = {"permission_denied", "no_focused_window", "focus_changed", "protected_surface", "ambiguous_window", "screen_permission_denied", "screenshot_unavailable", "screenshot_unusable", "window_too_large", "secure_field_visible"}
            code = payload.get("error")
            raise NativeFailure(code if code in allowed else "native_helper_failed")
        return payload

    def probe(self):
        return self._call("probe")

    def context(self):
        return self._call("context")

    def text(self, expected):
        return self._call("text", expected)

    def screenshot(self, expected):
        result = self._call("screenshot", expected)
        try:
            data = base64.b64decode(result["image_base64"], validate=True)
        except (ValueError, KeyError):
            raise NativeFailure("native_image_invalid") from None
        if not (data.startswith(b"\x89PNG\r\n\x1a\n") or data.startswith(b"\xff\xd8\xff")):
            raise NativeFailure("native_image_invalid")
        return data
