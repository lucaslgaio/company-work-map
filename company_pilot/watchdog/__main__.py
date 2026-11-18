"""Central company-source sync and inspection; no automatic startup."""
import argparse
import difflib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from threading import Event

from company_pilot.cli import _read_file, _safe_error, build_brain, load_config
from .archive import Archive, LocalObjects, SupabaseObjects, SyncError, sha
from .catalog import Catalog, synchronize
from .drive import DriveAPI, DriveSource, discover_shared_drives
from .extract import extract
from .local import DirectorySource, SQLiteSource


def credential(spec, name):
    """Read an explicit runtime credential reference, never search user accounts."""
    env_name, command = spec.get(name + "_env"), spec.get(name + "_command")
    if bool(env_name) == bool(command):
        raise SyncError("one_credential_reference_required")
    if env_name and not re.fullmatch(r"[A-Z][A-Z0-9_]+", env_name):
        raise SyncError("credential_environment_name_invalid")
    if command and (not isinstance(command, list) or not command or
                    any(not isinstance(arg, str) or not arg for arg in command)):
        raise SyncError("credential_command_argv_required")
    def read():
        if env_name:
            value = os.environ.get(env_name, "")
        else:
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=30)
            except (OSError, subprocess.TimeoutExpired):
                raise SyncError("credential_refresh_unavailable") from None
            if result.returncode:
                raise SyncError("credential_refresh_unavailable")
            value = result.stdout.strip()
        if not value or "\n" in value or "\r" in value:
            raise SyncError("credential_unavailable")
        return value
    return read


def read_settings(path):
    path = Path(path).expanduser().absolute()
    if path.is_symlink() or (os.name != "nt" and path.stat().st_mode & 0o077):
        raise SyncError("private_watchdog_configuration_required")
    settings = _read_file(path, json_value=True)
    allowed = {"schema_version", "company_config", "company_id", "catalog", "archive", "sources", "enabled",
               "source_scope_reference", "release_manifest", "interval_seconds"}
    if not isinstance(settings, dict) or set(settings) - allowed or settings.get("schema_version") != 1:
        raise SyncError("watchdog_configuration_invalid")
    if (type(settings.get("enabled", False)) is not bool or not isinstance(settings.get("sources"), list)
            or not settings["sources"] or not isinstance(settings.get("archive"), dict)):
        raise SyncError("watchdog_configuration_invalid")
    interval = settings.get("interval_seconds", 300)
    if type(interval) is not int or not 60 <= interval <= 86400:
        raise SyncError("watchdog_interval_invalid")
    seen = set()
    for source in settings["sources"]:
        if (not isinstance(source, dict) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", source.get("id", ""))
                or source["id"] in seen):
            raise SyncError("watchdog_source_identity_invalid")
        seen.add(source["id"])
        allowed_source = ({"id", "kind", "root"} if source.get("kind") == "directory" else
                          {"id", "kind", "path"} if source.get("kind") == "sqlite" else
                          {"id", "kind", "account_id", "token_env", "token_command"} if source.get("kind") == "google_drive" else set())
        if not allowed_source or set(source) - allowed_source:
            raise SyncError("watchdog_source_adapter_or_field_unsupported")
    archive = settings["archive"]
    archive_fields = ({"kind", "root"} if archive.get("kind") == "local" else
                      {"kind", "url", "bucket", "prefix", "api_key_env", "api_key_command", "bearer_env", "bearer_command"}
                      if archive.get("kind") == "supabase" else set())
    if not archive_fields or set(archive) - archive_fields:
        raise SyncError("watchdog_archive_configuration_invalid")
    return settings


def archive_for(settings, synthetic):
    value = settings["archive"]
    if value["kind"] == "local":
        if not synthetic:
            raise SyncError("local_archive_is_synthetic_only")
        return Archive(LocalObjects(value["root"]))
    if value.get("prefix") != settings.get("company_id"):
        raise SyncError("archive_company_prefix_mismatch")
    return Archive(SupabaseObjects(value["url"], value["bucket"], prefix=value["prefix"],
                                  api_key=credential(value, "api_key"), bearer=credential(value, "bearer")))


def _sources_for(settings, synthetic):
    for value in settings["sources"]:
        key = value["id"]
        if value["kind"] == "directory":
            root = Path(value["root"]).expanduser().resolve()
            yield DirectorySource(key + "/" + sha(str(root).encode())[:16], root, synthetic=synthetic)
        elif value["kind"] == "sqlite":
            path = Path(value["path"]).expanduser().resolve()
            yield SQLiteSource(key + "/" + sha(str(path).encode())[:16], path, synthetic=synthetic)
        else:
            if synthetic:
                raise SyncError("synthetic_configuration_cannot_export_real_drive")
            api = DriveAPI(credential(value, "token"))
            user = api.json("about", query={"fields": "user(permissionId,emailAddress)"}).get("user", {})
            if not value.get("account_id") or user.get("permissionId") != value["account_id"]:
                raise SyncError("company_drive_identity_mismatch")
            key += "/" + value["account_id"]
            # Discover every shared-drive page before treating the account's
            # user inventory as the complete configured source set.
            drives = discover_shared_drives(api)
            members = [key + "/user", *(key + "/shared/" + drive["id"] for drive in drives)]
            user_source = DriveSource(members[0], api)
            user_source.discovered_scope = {"prefix": key, "members": members}
            yield user_source
            for drive, member in zip(drives, members[1:]):
                source = DriveSource(member, api, drive_id=drive["id"])
                source.discovered_scope = user_source.discovered_scope
                yield source


def sources_for(settings, synthetic, *, discovery_result=None):
    for value in settings["sources"]:
        try:
            for source in _sources_for({"sources": [value]}, synthetic):
                if discovery_result:
                    discovery_result(value["id"], None)
                yield source
        except (SyncError, OSError) as error:
            if discovery_result is None:
                raise
            # Persist discovery/constructor failure even before a concrete
            # source can reach synchronize(); retain old snapshots as history.
            code = error.args[0] if isinstance(error, SyncError) else "source_discovery_failed"
            discovery_result(value["id"], code)


def gate(settings, brain, *, activation):
    snapshot = brain.snapshot()
    company = snapshot["company"]
    if settings.get("company_id") != company["id"]:
        raise SyncError("watchdog_company_identity_mismatch")
    synthetic = company.get("synthetic") is True
    if activation and not settings.get("enabled"):
        raise SyncError("watchdog_not_enabled")
    if not synthetic:
        if activation:
            manifest = _read_file(settings.get("release_manifest", ""), json_value=True)
            if manifest.get("release_status") != "pilot_ready":
                raise SyncError("watchdog_release_not_ready")
            if not isinstance(settings.get("source_scope_reference"), str) or not settings["source_scope_reference"].strip():
                raise SyncError("company_source_scope_not_confirmed")
        if brain.access_policy().get("state") != "verified":
            raise SyncError("company_access_policy_unverified")
        actor = brain.store.authenticated_actor()["id"]
        if actor not in company.get("watchdog_actors", []):
            raise SyncError("configured_watchdog_actor_required")
    return synthetic


def consumer_for(brain, archive, synthetic):
    def consume(record):
        result = extract(record, archive)
        # Keep Git evidence compact. The complete archive is inspectable; a
        # bounded excerpt is explicitly distinct from a full-file reading.
        excerpt = result["text"].encode()[:16 * 1024].decode("utf-8", errors="ignore")
        text = ("Registro da empresa: " + record.get("name", record["id"]) +
                "\nRevisão do provedor: " + record.get("provider_revision", "") +
                "\nEstado: " + record["state"] +
                "\nExtração: " + result["state"] +
                "; leitura integral: " + str(result["complete"] and len(excerpt) == len(result["text"])) +
                "\nEste registro mostra o conteúdo do sistema; não comprova execução ou aprovação por uma pessoa.\n" + excerpt)
        ref = record.get("archive")
        if isinstance(archive.objects, SupabaseObjects):
            base = archive.objects.url + "/storage/v1/object/" + archive.objects.bucket + "/" + archive.objects.prefix + "/"
        else:
            base = "local-fixture://archive/"
        receipt = brain.ingest_company_record({"schema_version": 1, "event_id": record["event_id"],
            "occurred_at": record["observed_at"], "synthetic": synthetic,
            "source": {"system": record["source_key"], "record_id": record["id"],
                       "url": record.get("original_url") or "urn:company-record:" + record["id"],
                       "revision": record["revision_id"]},
            "operation": "removed" if record["state"] == "removed" else "upsert",
            "content": {"text": text, "sha256": sha(text.encode()),
                        "complete": result["complete"] and len(excerpt) == len(result["text"])},
            "object": {"storage_ref": base + ref["key"], "sha256": ref["sha256"],
                       "media_type": record.get("mime_type", "application/octet-stream"), "size_bytes": ref["size"]} if ref else None})
        if not isinstance(receipt, dict) or not receipt.get("state"):
            raise SyncError("company_record_acknowledgement_unconfirmed")
    return consume


def main(argv=None, *, brain_factory=build_brain, out=None, err=None, stop=None):
    out, err, stop = out or sys.stdout, err or sys.stderr, stop or Event()
    parser = argparse.ArgumentParser(description="Sincronizar fontes da empresa e inspecionar suas versões.")
    parser.add_argument("--config", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    commands.add_parser("sync")
    watch = commands.add_parser("watch")
    watch.add_argument("--max-cycles", type=int)
    changes = commands.add_parser("changes")
    changes.add_argument("--source")
    changes.add_argument("--after", type=int, default=0, help="Última sequência já lida; zero começa o histórico.")
    changes.add_argument("--limit", type=int, default=50)
    inspect = commands.add_parser("inspect")
    inspect.add_argument("--source", required=True)
    inspect.add_argument("--record", required=True)
    inspect.add_argument("--revision", required=True)
    inspect.add_argument("--output-file", type=Path)
    compare = commands.add_parser("compare")
    compare.add_argument("--source", required=True)
    compare.add_argument("--record", required=True)
    compare.add_argument("--before", required=True)
    compare.add_argument("--after", required=True)
    catalog = None
    try:
        args = parser.parse_args(argv)
        settings = read_settings(args.config)
        catalog = Catalog(settings["catalog"])
        catalog.bind_company(settings.get("company_id"))
        if args.command == "status":
            result = catalog.status()
        elif args.command == "changes":
            if not 1 <= args.limit <= 500 or args.after < 0:
                raise SyncError("change_page_invalid")
            records = catalog.history(args.source, after=args.after, limit=args.limit + 1)
            page = records[:args.limit]
            result = {"changes": page, "has_more": len(records) > args.limit,
                      "next_after": page[-1]["sequence"] if page else args.after}
        else:
            brain = brain_factory(load_config(Path(settings["company_config"])))
            synthetic = gate(settings, brain, activation=args.command in ("sync", "watch"))
            archive = archive_for(settings, synthetic)
            if args.command == "compare":
                before = catalog.revision(args.source, args.record, args.before)
                after = catalog.revision(args.source, args.record, args.after)
                old_text, new_text = extract(before, archive), extract(after, archive)
                result = {"before": before, "after": after,
                          "before_extract": {key: value for key, value in old_text.items() if key != "text"},
                          "after_extract": {key: value for key, value in new_text.items() if key != "text"},
                          "diff": "\n".join(difflib.unified_diff(old_text["text"].splitlines(), new_text["text"].splitlines(),
                                                fromfile=args.before, tofile=args.after, lineterm="")),
                          "binary_hash_changed": (before.get("archive") or {}).get("sha256") != (after.get("archive") or {}).get("sha256")}
            elif args.command == "inspect":
                record = catalog.revision(args.source, args.record, args.revision)
                if args.output_file:
                    if not record.get("archive"):
                        raise SyncError("company_source_revision_has_no_binary")
                    # Export only to a new explicit private file, never overwrite.
                    with archive.open(record["archive"]) as stream:
                        with args.output_file.open("xb") as target:
                            os.chmod(args.output_file, 0o600)
                            import shutil
                            shutil.copyfileobj(stream, target)
                    result = {"state": "revision_exported", "sha256": record["archive"]["sha256"]}
                else:
                    result = {"record": record, "extract": extract(record, archive)}
            else:
                cycles = 0
                result = {"state": "stopped", "status": catalog.status()}
                while not stop.is_set():
                    gate(settings, brain, activation=True)
                    results = []
                    for source in sources_for(settings, synthetic, discovery_result=catalog.source_discovery_result):
                        try:
                            results.append(synchronize(source, catalog, archive,
                                           consumer=consumer_for(brain, archive, synthetic)))
                        except SyncError as error:
                            results.append({"source": source.key, "state": "pending", "error_code": error.args[0]})
                        finally:
                            if hasattr(source, "close"):
                                source.close()
                    result = {"results": results, "status": catalog.status()}
                    cycles += 1
                    if args.command == "sync" or (args.max_cycles is not None and cycles >= args.max_cycles):
                        break
                    print(json.dumps(result, ensure_ascii=False), file=out, flush=True)
                    if stop.wait(settings.get("interval_seconds", 300)):
                        break
        print(json.dumps(result, ensure_ascii=False), file=out, flush=True)
        pending = result.get("status", {}).get("sources", [])
        return 3 if result.get("status", {}).get("source_errors") or any(not row["content_complete"] or row["pending_map_records"] for row in pending) else 0
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        value = ({"ok": False, "codigo": "SINCRONIZACAO_PENDENTE", "subcodigo": error.args[0],
                  "acao": "Confira status, escopo, identidade e destino; preserve catálogo e arquivos. Nenhuma origem foi modificada."}
                 if isinstance(error, SyncError) else _safe_error(error))
        print(json.dumps(value, ensure_ascii=False), file=err, flush=True)
        return 2
    finally:
        if catalog:
            catalog.close()


if __name__ == "__main__":
    raise SystemExit(main())
