"""Read-only Drive v3 inventory/change/download adapter.

No token discovery, login, source mutation or download occurs on import.
"""
from contextlib import contextmanager
import http.client
import json
import re
import time
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

from .archive import SyncError

API = "https://www.googleapis.com/drive/v3/"
FIELDS = "id,name,mimeType,version,size,modifiedTime,parents,driveId,webViewLink,trashed,capabilities(canDownload),shortcutDetails(targetId,targetMimeType),resourceKey"
FOLDER = "application/vnd.google-apps.folder"
SHORTCUT = "application/vnd.google-apps.shortcut"


class CompleteDownload:
    """Verify byte counts even when HTTPResponse.read(n) quietly returns short."""
    def __init__(self, stream, expected_size=None):
        self.stream, self.size, self.eof = stream, 0, False
        header = stream.getheader("Content-Length") if hasattr(stream, "getheader") else None
        try:
            self.length = int(header) if header is not None else None
            self.expected_size = int(expected_size) if expected_size is not None else None
            if any(value is not None and value < 0 for value in (self.length, self.expected_size)):
                raise ValueError()
        except (TypeError, ValueError):
            raise SyncError("drive_download_length_invalid") from None

    def read(self, size=-1):
        try:
            data = self.stream.read(size)
        except (http.client.HTTPException, OSError):
            raise SyncError("drive_download_incomplete") from None
        self.size += len(data)
        if not data or size is None or size < 0:
            self.eof = True
            self.verify()
        return data

    def verify(self):
        if not self.eof or any(value is not None and self.size != value for value in (self.length, self.expected_size)):
            raise SyncError("drive_download_incomplete")


def _google_download(url):
    part = urlsplit(url)
    host = part.hostname or ""
    if (part.scheme != "https" or part.username or part.password or part.port not in (None, 443)
            or not (host in ("www.googleapis.com", "drive.google.com", "drive.usercontent.google.com")
                    or host.endswith(".googleusercontent.com"))):
        raise SyncError("drive_download_origin_invalid")


class GoogleRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _google_download(newurl)
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected and urlsplit(newurl).hostname != urlsplit(req.full_url).hostname:
            redirected.remove_header("Authorization")
            redirected.remove_header("X-goog-drive-resource-keys")
        return redirected


class DriveAPI:
    def __init__(self, token, *, opener=None):
        self.token = token
        self.opener = opener or build_opener(GoogleRedirect())

    def open(self, route, *, query=None, method="GET", resource=None, absolute=False):
        if absolute:
            _google_download(route)
            url = route
        else:
            if not re.fullmatch(r"[A-Za-z0-9_./-]+", route) or ".." in route:
                raise SyncError("drive_api_route_invalid")
            url = API + route
        if query:
            url += ("&" if "?" in url else "?") + urlencode(query)
        headers = {}
        if urlsplit(url).hostname in ("www.googleapis.com", "drive.google.com", "drive.usercontent.google.com"):
            headers["Authorization"] = "Bearer " + self.token()
        if resource:
            headers["X-Goog-Drive-Resource-Keys"] = resource
        try:
            return self.opener.open(Request(url, method=method, headers=headers,
                                            data=b"" if method == "POST" else None), timeout=60)
        except HTTPError as error:
            code = error.code
            error.close()
            raise SyncError("drive_http_" + str(code)) from None
        except SyncError:
            raise
        except (OSError, TimeoutError):
            raise SyncError("drive_connection_unconfirmed") from None

    def json(self, route, **kwargs):
        with self.open(route, **kwargs) as response:
            data = response.read(8 * 1024 * 1024 + 1)
        if len(data) > 8 * 1024 * 1024:
            raise SyncError("drive_metadata_page_oversize")
        try:
            return json.loads(data)
        except (ValueError, UnicodeError):
            raise SyncError("drive_metadata_invalid") from None


def discover_shared_drives(api):
    """All pages for this configured identity, not an assertion about a domain."""
    drives, page, visited = [], None, set()
    while True:
        query = {"pageSize": 100, "fields": "nextPageToken,drives(id,name)"}
        if page:
            query["pageToken"] = page
        result = api.json("drives", query=query)
        drives.extend(result.get("drives", []))
        page = result.get("nextPageToken")
        if not page:
            return drives
        if page in visited:
            raise SyncError("drive_pagination_cycle")
        visited.add(page)


class DriveSource:
    def __init__(self, key, api, *, drive_id=None, synthetic=False, sleep=time.sleep):
        self.key, self.api, self.drive_id, self.synthetic = key, api, drive_id, synthetic
        self.sleep, self.operations = sleep, {}

    def _scope(self):
        query = {"supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}
        if self.drive_id:
            query["driveId"] = self.drive_id
        return query

    def _entry(self, file):
        if not file.get("id") or not file.get("version"):
            raise SyncError("drive_file_identity_missing")
        state = "removed" if file.get("trashed") else "metadata_only" if file["mimeType"] in (FOLDER, SHORTCUT) else "present"
        return {"id": file["id"], "name": file.get("name", ""), "mime_type": file["mimeType"],
                "provider_revision": str(file["version"]), "modified_at": file.get("modifiedTime"),
                "parents": file.get("parents", []), "original_url": file.get("webViewLink") or
                "https://drive.google.com/file/d/" + quote(file["id"], safe=""),
                "state": state, "synthetic": self.synthetic,
                "size_bytes": file.get("size") if not file["mimeType"].startswith("application/vnd.google-apps.") else None,
                "can_download": file.get("capabilities", {}).get("canDownload", False),
                "resource_key": file.get("resourceKey"), "shortcut": file.get("shortcutDetails")}

    def get(self, item):
        return self._entry(self.api.json("files/" + quote(item, safe=""),
                                         query={"fields": FIELDS, "supportsAllDrives": "true"}))

    def scan(self, cursor):
        # Capture the change watermark BEFORE listing so edits during a long
        # first import are caught up rather than lost between two phases.
        if cursor is None:
            scope = {"supportsAllDrives": "true"}
            if self.drive_id:
                scope["driveId"] = self.drive_id
            cursor = self.api.json("changes/startPageToken", query=scope)["startPageToken"]
            page, visited = None, set()
            while True:
                query = {**self._scope(), "corpora": "drive" if self.drive_id else "user",
                         "q": "trashed = false", "pageSize": 1000,
                         "fields": "nextPageToken,incompleteSearch,files(" + FIELDS + ")"}
                if page:
                    query["pageToken"] = page
                result = self.api.json("files", query=query)
                if result.get("incompleteSearch"):
                    raise SyncError("drive_inventory_incomplete")
                for file in result.get("files", []):
                    # Shared drives have their own explicitly discovered scope,
                    # avoiding duplicate catalogue rows in the user inventory.
                    if self.drive_id or not file.get("driveId"):
                        yield self._entry(file)
                page = result.get("nextPageToken")
                if not page:
                    break
                if page in visited:
                    raise SyncError("drive_pagination_cycle")
                visited.add(page)
        visited = set()
        while True:
            result = self.api.json("changes", query={**self._scope(), "pageToken": cursor,
                "pageSize": 1000, "includeRemoved": "true",
                "fields": "nextPageToken,newStartPageToken,changes(fileId,removed,file(" + FIELDS + "))"})
            for change in result.get("changes", []):
                if change.get("removed"):
                    # Drive removed also covers loss of access. Do not claim a
                    # deletion or erase previous evidence from this signal.
                    yield {"id": change["fileId"], "state": "removed", "provider_revision": "removed:" + cursor,
                           "removal_meaning": "removed_or_access_lost", "synthetic": self.synthetic}
                elif change.get("file") and (self.drive_id or not change["file"].get("driveId")):
                    yield self._entry(change["file"])
            following = result.get("nextPageToken")
            if not following:
                if not result.get("newStartPageToken"):
                    raise SyncError("drive_change_checkpoint_missing")
                return result["newStartPageToken"]
            if following in visited:
                raise SyncError("drive_pagination_cycle")
            visited.add(following)
            cursor = following

    @contextmanager
    def open(self, entry):
        if not entry.get("can_download"):
            raise SyncError("drive_download_not_permitted")
        route = "files/" + quote(entry["id"], safe="")
        resource = entry["id"] + "/" + entry["resource_key"] if entry.get("resource_key") else None
        if entry["mime_type"].startswith("application/vnd.google-apps."):
            # LRO downloads support larger native files than files.export's
            # 10MB cap. Preserve Google's native default Office/ZIP export.
            cache_key = (entry["id"], entry["provider_revision"])
            operation = self.operations.get(cache_key)
            if operation is None:
                operation = self.api.json(route + "/download", method="POST", resource=resource)
            for attempt in range(6):
                self.operations[cache_key] = operation
                if operation.get("done"):
                    break
                name = operation.get("name", "")
                if not re.fullmatch(r"operations/[A-Za-z0-9_.-]+", name):
                    raise SyncError("drive_download_operation_invalid")
                self.sleep(10)
                operation = self.api.json(name, resource=resource)
            if not operation.get("done"):
                raise SyncError("drive_download_pending")
            if operation.get("error"):
                self.operations.pop(cache_key, None)
                raise SyncError("drive_download_operation_failed")
            uri = operation.get("response", {}).get("downloadUri")
            if not isinstance(uri, str):
                raise SyncError("drive_download_uri_missing")
            with self.api.open(uri, absolute=True, resource=resource) as stream:
                complete = CompleteDownload(stream)
                yield complete
                complete.verify()
            self.operations.pop(cache_key, None)
        else:
            with self.api.open(route, query={"alt": "media", "supportsAllDrives": "true"}, resource=resource) as stream:
                complete = CompleteDownload(stream, entry.get("size_bytes"))
                yield complete
                complete.verify()

    def verify(self, entry):
        current = self.get(entry["id"])
        if current != entry:
            raise SyncError("source_changed_during_read")
