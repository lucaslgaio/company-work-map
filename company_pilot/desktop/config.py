"""Configuration primitives, independent of online enrollment/storage choice."""
import json
import os
from pathlib import Path
import uuid
import tempfile

from .policy import Policy


def read_config(path: Path):
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ValueError("config_schema_invalid")
    uuid.UUID(raw["device_id"])
    for key in ("policy_file", "outbox_file"):
        if not isinstance(raw.get(key), str) or not Path(raw[key]).is_absolute():
            raise ValueError("config_requires_absolute_path")
    interval = raw.get("poll_seconds", 15)
    if isinstance(interval, bool) or not isinstance(interval, (float, int)) or not 3 <= interval <= 3600:
        raise ValueError("config_poll_interval_invalid")
    return raw


def policy_file(path: Path):
    return Policy.parse(json.loads(Path(path).read_text(encoding="utf-8")))


def write_config(path: Path, raw: dict):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Enrollment/update must not silently replace an existing device config.
    with path.open("x", encoding="utf-8") as stream:
        os.chmod(path, 0o600)
        json.dump(raw, stream, ensure_ascii=False, indent=2)


def replace_config(path: Path, raw: dict):
    path = Path(path)
    descriptor, temporary = tempfile.mkstemp(prefix="config-", dir=path.parent)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(raw, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)

