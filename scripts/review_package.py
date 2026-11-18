"""Build a review zip: README, MANIFEST and the source bundle. Nothing is deployed."""
import hashlib
import json
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP = {".git", "__pycache__", ".pytest_cache", "node_modules"}


def stamped_release(raw, commit):
    """The git file may say HEAD. The review package records the real SHA."""
    release = json.loads(raw)
    if not isinstance(commit, str) or len(commit) != 40 or any(char not in "0123456789abcdef" for char in commit):
        raise ValueError("real_commit_required")
    release["code_commit"] = commit
    release["code_commit_resolution"] = "SHA gravado na montagem deste pacote."
    return (json.dumps(release, ensure_ascii=False, indent=2) + "\n").encode()


def main():
    commit = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    out = Path("/opt/cursor/artifacts")
    out.mkdir(parents=True, exist_ok=True)
    bundle = out / f"company-work-map-review-{commit[:12]}.zip"
    readme = (
        "# Pacote de revisão\n\n"
        f"Commit: `{commit}`\n\n"
        "Este pacote é o código. Não inclui empresa de exemplo, não envia WhatsApp e não liga captura.\n\n"
        "Comece por `START-HERE.md`.\n"
    )
    files = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or any(part in SKIP for part in path.parts):
            continue
        files.append(path)
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("README.md", readme)
        manifest = ["# MANIFEST\n", f"commit {commit}\n"]
        for path in files:
            rel = path.relative_to(ROOT).as_posix()
            raw = path.read_bytes()
            if rel == "founder/pilot-release.json":
                raw = stamped_release(raw, commit)
            digest = hashlib.sha256(raw).hexdigest()
            manifest.append(f"{digest}  {rel}\n")
            archive.writestr(f"source/{rel}", raw)
        archive.writestr("MANIFEST", "".join(manifest))
    print(bundle)


if __name__ == "__main__":
    main()
