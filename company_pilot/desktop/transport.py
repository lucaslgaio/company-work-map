"""Transport seam and an explicitly synthetic on-disk development receiver."""
from hashlib import sha256
import json
import os
from pathlib import Path
import uuid

from .collector import PermanentDeliveryError
from .outbox import canonical


class DirectoryFixtureTransport:
    """Controlled proof receiver only. This is never an online company brain."""
    def __init__(self, path: Path):
        self.root = Path(path)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _put(self, path: Path, data: bytes):
        if path.exists():
            if path.read_bytes() != data:
                raise PermanentDeliveryError("fixture_id_payload_conflict")
            return
        temporary = path.with_name(path.name + "." + str(uuid.uuid4()) + ".tmp")
        with temporary.open("xb") as stream:
            os.chmod(temporary, 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # Concurrent receivers may race. Link refuses replacement of an existing receipt.
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != data:
                raise PermanentDeliveryError("fixture_id_payload_conflict")
        finally:
            temporary.unlink()

    def upload_attachment(self, event_id: str, data: bytes, digest: str):
        if sha256(data).hexdigest() != digest:
            raise PermanentDeliveryError("attachment_hash_mismatch")
        opaque = str(uuid.uuid5(uuid.UUID(event_id), digest))
        self._put(self.root / (opaque + ".png"), data)
        return {"id": opaque, "sha256": digest}

    def send_observation(self, payload: dict):
        event_id = str(uuid.UUID(payload["event_id"]))
        self._put(self.root / (event_id + ".json"), canonical(payload))
        return {"observation_id": event_id, "inspect_url": "local-fixture:" + event_id, "development_fixture": True}

    def probe(self):
        return {"online": False, "authenticated": False, "development_fixture": True}


def make_transport(config: dict, development: bool = False):
    kind = config.get("kind")
    if kind == "directory-fixture" and development:
        return DirectoryFixtureTransport(Path(config["path"]))
    if kind == "github":
        from .github_transport import GitHubTransport
        return GitHubTransport(config)
    # The brain owner plugs its selected authenticated adapter into this seam.
    raise ValueError("online_transport_not_configured")
