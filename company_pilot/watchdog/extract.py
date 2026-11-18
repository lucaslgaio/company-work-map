"""Bounded readable extracts; an archived binary is not a fully read document."""
import codecs
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import xml.etree.ElementTree as ET
import zipfile

from .archive import SyncError

LIMIT = 240 * 1024
XML_LIMIT = 8 * 1024 * 1024


def extract(record, archive):
    reference = record.get("archive")
    if not reference:
        return {"text": "", "state": "metadata_only", "complete": False}
    mime = record.get("mime_type", "")
    office = ("application/vnd.openxmlformats-officedocument." in mime or
              mime in {"application/vnd.google-apps.document", "application/vnd.google-apps.spreadsheet",
                       "application/vnd.google-apps.presentation"})
    if not (mime.startswith("text/") or office or mime in
            {"application/json", "application/xml", "application/vnd.sqlite3"}):
        return {"text": "", "state": "binary_not_extracted", "complete": False}
    parts, size, truncated = [], 0, False
    def add(text):
        nonlocal size, truncated
        data = text.encode("utf-8")
        remaining = LIMIT - size
        if len(data) > remaining:
            data = data[:remaining]
            truncated = True
        value = data.decode("utf-8", errors="ignore")
        if value:
            parts.append(value)
        size += len(data)
        return not truncated
    try:
        with archive.open(reference) as stream:
            if mime == "application/vnd.sqlite3":
                with tempfile.TemporaryDirectory() as folder:
                    path = Path(folder) / "snapshot.db"
                    with path.open("wb") as out:
                        shutil.copyfileobj(stream, out)
                    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
                        db.execute("PRAGMA query_only=ON")
                        db.execute("PRAGMA trusted_schema=OFF")
                        tables = db.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
                        for (table,) in tables:
                            query = db.execute('SELECT * FROM "' + table.replace('"', '""') + '"')
                            if not add("\nTabela " + table + ": " + json.dumps([col[0] for col in query.description], ensure_ascii=False) + "\n"):
                                break
                            for row in query:
                                # BLOBs stay archived; never imply their body was
                                # understood from a size or Python byte repr.
                                row = [{"blob_bytes": len(value)} if isinstance(value, bytes) else value for value in row]
                                if not add(json.dumps(row, ensure_ascii=False) + "\n"):
                                    break
                            if truncated:
                                break
                return {"text": "".join(parts), "state": "database_rows", "complete": False,
                        "truncated": truncated, "limit": LIMIT}
            if zipfile.is_zipfile(stream):
                stream.seek(0)
                with zipfile.ZipFile(stream) as package:
                    names = package.namelist()
                    selected = [name for name in names if name == "word/document.xml" or
                                (name.startswith("ppt/slides/slide") and name.endswith(".xml")) or
                                (name.startswith("xl/worksheets/sheet") and name.endswith(".xml"))]
                    if not selected:
                        return {"text": "", "state": "binary_not_extracted", "complete": False}
                    shared = []
                    if "xl/sharedStrings.xml" in names:
                        info = package.getinfo("xl/sharedStrings.xml")
                        if info.file_size > XML_LIMIT:
                            return {"text": "", "state": "extract_component_oversize", "complete": False}
                        shared = ["".join(node.itertext()) for node in ET.fromstring(package.read(info))]
                    for name in sorted(selected):
                        info = package.getinfo(name)
                        if info.file_size > XML_LIMIT:
                            truncated = True
                            continue
                        root = ET.fromstring(package.read(info))
                        if not add("\nParte " + name + "\n"):
                            break
                        if name.startswith("xl/"):
                            for cell in root.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}c"):
                                value = cell.find("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}v")
                                formula = cell.find("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}f")
                                text = value.text or "" if value is not None else "".join(cell.itertext())
                                if cell.get("t") == "s":
                                    index = int(text)
                                    text = shared[index] if 0 <= index < len(shared) else "[referência ausente]"
                                if formula is not None:
                                    text = "fórmula=" + (formula.text or "") + "; valor registrado=" + text
                                if not add(cell.get("r", "") + "=" + text + "\n"):
                                    break
                        else:
                            for node in root.iter():
                                if node.tag.rsplit("}", 1)[-1] == "t" and node.text:
                                    if not add(node.text + "\n"):
                                        break
                        if truncated:
                            break
                return {"text": "".join(parts), "state": "office_text_extract", "complete": False,
                        "truncated": truncated, "limit": LIMIT}
            stream.seek(0)
            if mime.startswith("text/") or mime in ("application/json", "application/xml"):
                data = stream.read(LIMIT + 1)
                text = codecs.getincrementaldecoder("utf-8")().decode(data[:LIMIT], final=len(data) <= LIMIT)
                return {"text": text, "state": "utf8_text", "complete": len(data) <= LIMIT,
                        "truncated": len(data) > LIMIT, "limit": LIMIT}
            return {"text": "", "state": "binary_not_extracted", "complete": False}
    except SyncError:
        raise
    except (ValueError, UnicodeError, zipfile.BadZipFile, ET.ParseError, sqlite3.Error, KeyError):
        return {"text": "", "state": "extract_unavailable", "complete": False}
