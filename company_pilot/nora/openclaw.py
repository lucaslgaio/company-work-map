"""Inactive-by-default OpenClaw transport and strict bridge-event decoder.

No connection, process or send happens at import/construction. A caller must supply
the separately verified route and invoke Nora.tick with this sender explicitly.
"""
from datetime import datetime
import json
from pathlib import Path
import subprocess
from typing import Callable

from .models import InboundEvent, KnownUnsent, Route, SendReceipt


class OpenClawSender:
    def __init__(self, executable: str, binding: Route, *, timeout_seconds: int = 30,
                 runner: Callable = subprocess.run):
        if not Path(executable).is_absolute() or not 1 <= timeout_seconds <= 30:
            raise ValueError("An absolute pinned executable and bounded timeout are required")
        self.executable = executable
        self.binding = binding
        self.timeout_seconds = timeout_seconds
        self.runner = runner

    def send(self, route: Route, text: str) -> SendReceipt:
        if not route.enabled or route != self.binding or not text.strip():
            raise KnownUnsent("Route not explicitly enabled/bound")
        args = [self.executable, "message", "send", "--channel", "whatsapp",
                "--account", route.account, "--target", route.conversation,
                "--json", "-m", text]
        try:
            result = self.runner(args, capture_output=True, text=True,
                                 timeout=self.timeout_seconds, check=False)
        except (FileNotFoundError, PermissionError):
            raise KnownUnsent("Pinned executable could not start") from None
        except Exception:
            # Never leak an exception whose command includes the message body.
            raise RuntimeError("OpenClaw outcome unknown") from None
        if result.returncode != 0:
            # A nonzero exit can occur after an external effect. No safe retry claim.
            raise RuntimeError("OpenClaw outcome unknown")
        try:
            raw = result.stdout
            data = json.loads(raw[raw.index("{"):])
            message_id = data.get("messageId") or data.get("payload", {}).get("result", {}).get("messageId")
            if not isinstance(message_id, str) or not message_id.strip():
                raise ValueError("Missing receipt")
        except (ValueError, TypeError, AttributeError):
            raise RuntimeError("OpenClaw receipt missing") from None
        return SendReceipt(route.account, route.conversation, message_id)


def inbound_from_record(record: dict) -> InboundEvent:
    """Decode records from bridge/index.js. File ownership/ingress is trusted.

    This decoder is not an unauthenticated webhook handler. An online wrapper must
    authenticate its adapter and derive this record from trusted channel metadata.
    """
    required = ("account", "conversation", "sender", "message_id", "received_at")
    if record.get("schema_version") != 1 or any(
            not isinstance(record.get(key), str) or not record[key].strip() for key in required):
        raise ValueError("Incomplete trusted bridge event")
    if not isinstance(record.get("text"), str):
        raise ValueError("Bridge text must be a string, even for a media marker")
    media_type = record.get("media_type")
    if media_type is not None and (not isinstance(media_type, str) or "/" not in media_type):
        raise ValueError("Invalid media marker")
    if not record["text"].strip() and not media_type:
        raise ValueError("Reply needs text or a visible media marker")
    if record.get("reply_to") is not None and not isinstance(record["reply_to"], str):
        raise ValueError("Invalid reply ID")
    received_at = datetime.fromisoformat(record["received_at"].replace("Z", "+00:00"))
    if received_at.tzinfo is None:
        raise ValueError("Bridge time needs a timezone")
    return InboundEvent(*(record[key] for key in required[:4]), received_at,
                        record["text"], record.get("reply_to"), media_type)
