# -*- coding: utf-8 -*-
# ============================================================
#  GOOGLE DRIVE STORAGE  —  Colab, Kaggle & Local
#  Drop-in compatible with your training notebook
#  Uses: storage.ensure_local(), storage.sync_to_drive()
#
# One-time setup reminder
# Environment   | What you need
# Colab         | Just run — Drive mounts automatically
# Kaggle        | Notebook Settings → Secrets → add GDRIVE_SA_KEY (paste full service_account.json)
# Local (easy)  | client_secrets.json in same folder, browser popup once
# Local (server)| service_account.json at SA_KEY_PATH
# ============================================================

import os
import json
import fnmatch
import logging
import time
import socket
import hashlib
import re
import shutil
import uuid
from pathlib import Path
from typing import Optional
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_EXCEPTION


# ── LOGGER ───────────────────────────────────────────────────
log = logging.getLogger(__name__)

# ── Try loading .env if available ────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ── Environment detection ─────────────────────────────────────
# Environment Detection
def _is_kaggle() -> bool:
    return os.environ.get("KAGGLE_KERNEL_RUN_TYPE") is not None

def _is_colab() -> bool:
    if _is_kaggle():
        return False
    try:
        import google.colab
        return True
    except ImportError:
        return False

IN_KAGGLE = _is_kaggle()
IN_COLAB  = _is_colab()
IN_LOCAL  = not IN_COLAB and not IN_KAGGLE

# ── CONFIG (edit these or set via env / constructor args) ─────
_DEFAULT_SA_KEY_PATH        = os.environ.get("SA_KEY_PATH", "client_secrets.json")
_DEFAULT_GDRIVE_AUTH_METHOD = os.environ.get("GDRIVE_AUTH_METHOD", "oauth2")
_DEFAULT_KAGGLE_SECRET      = os.environ.get("GDRIVE_SA_KEY", "GDRIVE_SA_KEY")
_DEFAULT_PROJECT_FOLDER     = os.environ.get("PROJECT_FOLDER", "my_project")
_DEFAULT_GDRIVE_FOLDER_ID   = os.environ.get("GDRIVE_FOLDER_ID", os.environ.get("GDRIVE_FOLDER", "your_folder_id"))

if IN_KAGGLE:
    try:
        from kaggle_secrets import UserSecretsClient
        _secrets = UserSecretsClient()
        _DEFAULT_PROJECT_FOLDER   = _secrets.get_secret("PROJECT_FOLDER") or _DEFAULT_PROJECT_FOLDER
        _DEFAULT_GDRIVE_FOLDER_ID = _secrets.get_secret("GDRIVE_FOLDER_ID") or _DEFAULT_GDRIVE_FOLDER_ID
        _DEFAULT_GDRIVE_AUTH_METHOD = "service_account"
        _DEFAULT_SA_KEY_PATH        = "service_account.json"
    except Exception:
        pass
elif IN_COLAB:
    _DEFAULT_GDRIVE_AUTH_METHOD = "oauth2"
    try:
        from google.colab import userdata
        _DEFAULT_PROJECT_FOLDER     = userdata.get("PROJECT_FOLDER") or _DEFAULT_PROJECT_FOLDER
        _DEFAULT_GDRIVE_FOLDER_ID   = userdata.get("GDRIVE_FOLDER") or _DEFAULT_GDRIVE_FOLDER_ID
    except Exception:
        pass
else:
    # Local defaults are already set by the base definitions above.
    pass

_DEFAULT_MAX_WORKERS        = 1

# ════════════════════════════════════════════════════════════
#  PATH HELPERS
# ════════════════════════════════════════════════════════════

def _build_base_path(project_folder: str) -> Path:
    """Return and create the environment-appropriate base path."""
    if IN_COLAB:
        gdrive_env = os.environ.get("GDRIVE_FOLDER")
        if gdrive_env:
            if gdrive_env.endswith(f"/{project_folder}") or gdrive_env == project_folder:
                base = (Path(gdrive_env) if gdrive_env.startswith("/")
                        else Path(f"/content/drive/MyDrive/{gdrive_env}"))
            else:
                base = (Path(gdrive_env) / project_folder if gdrive_env.startswith("/")
                        else Path(f"/content/drive/MyDrive/{gdrive_env}") / project_folder)
        else:
            base = Path(f"/content/drive/MyDrive/{project_folder}")
        # Don't mkdir here — Drive must be mounted first (see _ensure_colab_mount)
    elif IN_KAGGLE:
        base = Path("/kaggle/working") / project_folder
        base.mkdir(parents=True, exist_ok=True)
    else:
        base = Path(f"~/gdrive_local/{project_folder}").expanduser()
        base.mkdir(parents=True, exist_ok=True)
    return base


def _ensure_colab_mount(base_path: Path) -> None:
    """Mount Google Drive in Colab if not already mounted, then create base_path."""
    if not IN_COLAB:
        return
    if not Path("/content/drive").exists():
        from google.colab import drive          # type: ignore
        drive.mount("/content/drive")
    base_path.mkdir(parents=True, exist_ok=True)


# ════════════════════════════════════════════════════════════
#  DRIVE API QUERY ESCAPING & VALIDATION
# ════════════════════════════════════════════════════════════

def _md5_of(path: Path) -> str:
    """Return the hex MD5 digest of a local file."""
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

_FOLDER_ID_RE = re.compile(r'^[a-zA-Z0-9_-]+$')

def _escape_drive_query(value: str) -> str:
    """Escape single quotes for Drive API query strings."""
    return value.replace("\\", "\\\\").replace("'", "\\'")

def _validate_folder_id(folder_id: str) -> str:
    """Validate that a Drive folder ID matches expected characters."""
    if not folder_id or not _FOLDER_ID_RE.match(folder_id):
        raise ValueError(f"Invalid folder ID: {folder_id!r}")
    return folder_id

def _optimal_chunksize(file_size: int) -> int:
    """Return a chunk size (multiple of 256 KB) scaled to file size.

    - < 5 MB  → not used (simple upload path)
    - < 50 MB → 2 MB chunks
    - < 200 MB→ 5 MB chunks
    - else    → 10 MB chunks
    """
    MB = 1024 * 1024
    if file_size < 50 * MB:
        return 2 * MB      # 2 097 152 — 8 × 256 KB
    elif file_size < 200 * MB:
        return 5 * MB      # 5 242 880 — 20 × 256 KB
    else:
        return 10 * MB     # 10 485 760 — 40 × 256 KB


def _files_match(src: Path, dest: Path) -> bool:
    """Fast comparison: size + mtime (falls back to partial/full content check)."""
    if not dest.exists():
        return False
    src_stat = src.stat()
    dest_stat = dest.stat()
    if src_stat.st_size != dest_stat.st_size:
        return False
    if abs(src_stat.st_mtime - dest_stat.st_mtime) < 1.0:
        return True
    size = src_stat.st_size
    if size < 10 * 1024 * 1024:
        return _md5_of(src) == _md5_of(dest)
    sample_size = 64 * 1024
    with open(src, "rb") as fs, open(dest, "rb") as fd:
        if fs.read(sample_size) != fd.read(sample_size):
            return False
        if size > sample_size:
            fs.seek(-sample_size, 2)
            fd.seek(-sample_size, 2)
            if fs.read(sample_size) != fd.read(sample_size):
                return False
    return True

def _should_skip_upload(local_path: Path, remote_info: Optional[dict]) -> bool:
    """Return True when the remote file is byte-identical to the local file."""
    if not remote_info:
        return False
    local_size = local_path.stat().st_size
    remote_size = remote_info.get("size", -1)
    if remote_size != local_size:
        return False
    remote_md5 = remote_info.get("md5Checksum")
    if not remote_md5:
        return True  # no md5 available, size matches — skip
    return _md5_of(local_path) == remote_md5

# ════════════════════════════════════════════════════════════
#  AUTH + DRIVE SERVICE  (per-instance, not a module-level global)
# ════════════════════════════════════════════════════════════

def _build_drive_service(sa_key_path: str, kaggle_secret: str, auth_method: str,
                         http_timeout: int = 300):
    """
    Build and return an authenticated Drive service object.
    Returns None if running in Colab (Drive is mounted — no API needed).
    Raises RuntimeError with a clear message if credentials cannot be found.
    """
    if IN_COLAB:
        return None  # Colab uses mounted Drive, not the API

    from googleapiclient.discovery import build
    import httplib2
    from google_auth_httplib2 import AuthorizedHttp

    if IN_KAGGLE:
        from kaggle_secrets import UserSecretsClient
        from google.oauth2 import service_account
        raw = UserSecretsClient().get_secret(kaggle_secret)
        creds_dict = json.loads(raw)
        creds = service_account.Credentials.from_service_account_info(
            creds_dict, scopes=["https://www.googleapis.com/auth/drive"])
        log.info("🔑 Kaggle: service account from secret %s", kaggle_secret)

    else:  # IN_LOCAL
        from google.auth.transport.requests import Request

        sa_path    = Path(sa_key_path).expanduser()
        token_file = Path("~/.gdrive_token.json").expanduser()

        if auth_method == "service_account":
            from google.oauth2 import service_account
            if not sa_path.exists():
                raise RuntimeError(
                    f"Service account key not found at '{sa_path}'. "
                    "Set SA_KEY_PATH or pass sa_key_path= to CommonStorage."
                )
            creds = service_account.Credentials.from_service_account_file(
                str(sa_path), scopes=["https://www.googleapis.com/auth/drive"])
            log.info("🔑 Local: service account from %s", sa_path)
        else:
            creds = None
            if token_file.exists():
                with open(token_file, "r") as f:
                    from google.oauth2.credentials import Credentials
                    creds = Credentials.from_authorized_user_info(json.load(f))
            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                else:
                    from google_auth_oauthlib.flow import InstalledAppFlow
                    creds = InstalledAppFlow.from_client_secrets_file(
                        "client_secrets.json",
                        ["https://www.googleapis.com/auth/drive"],
                    ).run_local_server(port=0)
                with open(token_file, "w") as f:
                    f.write(creds.to_json())
            log.info("🔑 Local: OAuth2 token")

    # Use httplib2 with an explicit timeout so large uploads
    # don't fail with TimeoutError on slow/unstable connections.
    http_obj = httplib2.Http(timeout=http_timeout)
    
    authed_http = AuthorizedHttp(creds, http=http_obj)
    log.info("⏱️  HTTP timeout set to %ds", http_timeout)
    return build("drive", "v3", http=authed_http)


# ════════════════════════════════════════════════════════════
#  RETRY HELPER
# ════════════════════════════════════════════════════════════

def _with_retry(fn, retries: int = 5, backoff: float = 1.0):
    """Call fn(), retrying on Drive API rate-limit / transient errors."""
    try:
        from googleapiclient.errors import HttpError
    except ImportError:
        HttpError = None
    for attempt in range(retries):
        try:
            return fn()
        except Exception as exc:
            if HttpError and isinstance(exc, HttpError):
                status = exc.resp.status
                if status in (403, 429, 500, 502, 503, 504) and attempt < retries - 1:
                    wait_sec = backoff * (2 ** attempt)
                    log.warning(
                        "Drive API %s — retrying in %.1fs (attempt %d/%d)",
                        status, wait_sec, attempt + 1, retries,
                    )
                    time.sleep(wait_sec)
                else:
                    raise
            elif isinstance(exc, (ConnectionError, socket.timeout, OSError)):
                if attempt < retries - 1:
                    wait_sec = backoff * (2 ** attempt)
                    log.warning(
                        "Network error %s — retrying in %.1fs (attempt %d/%d)",
                        type(exc).__name__, wait_sec, attempt + 1, retries,
                    )
                    time.sleep(wait_sec)
                else:
                    raise
            else:
                raise



# ════════════════════════════════════════════════════════════
#  INCLUDE / EXCLUDE FILTER
# ════════════════════════════════════════════════════════════

def _should_sync(
    item_path: Path,
    include_patterns: list,
    exclude_patterns: list,
    *,
    is_dir: bool = False,
) -> bool:
    name     = item_path.name
    path_str = str(item_path)

    # ── 1. Exclude check ────────────────────────────────────
    for pat in exclude_patterns:
        if fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(path_str, pat):
            return False

    # ── 2. Include gate ─────────────────────────────────────
    if include_patterns:
        # Always descend into directories so nested includes are reachable
        if is_dir:
            return True
        if not any(
            fnmatch.fnmatch(name, p) or fnmatch.fnmatch(path_str, p)
            for p in include_patterns
        ):
            return False

    return True


# ════════════════════════════════════════════════════════════
#  LOW-LEVEL DRIVE API HELPERS  (all take svc explicitly)
# ════════════════════════════════════════════════════════════

def _get_or_create_folder(svc, name: str, parent_id: str) -> str:
    safe = _escape_drive_query(name)
    parent_id = _validate_folder_id(parent_id)
    q = (f"name='{safe}' and '{parent_id}' in parents "
         f"and mimeType='application/vnd.google-apps.folder' and trashed=false")
    hits = _with_retry(
        lambda q_str=q: svc.files().list(q=q_str, fields="files(id)", pageSize=2, supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
    ).get("files", [])
    if hits:
        return hits[0]["id"]
    try:
        return _with_retry(
            lambda n=name, p=parent_id: svc.files().create(
                supportsAllDrives=True,
                body={
                    "name": n,
                    "parents": [p],
                    "mimeType": "application/vnd.google-apps.folder",
                },
                fields="id",
            ).execute()
        )["id"]
    except Exception:
        hits = _with_retry(
            lambda q_str=q: svc.files().list(q=q_str, fields="files(id)", pageSize=2, supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
        ).get("files", [])
        if hits:
            log.debug("Folder '%s' found after create conflict — using existing.", name)
            return hits[0]["id"]
        raise


def _list_children_raw(svc, folder_id: str, fields: str = "id, name, mimeType") -> list:
    """List all children in a Drive folder with pagination."""
    all_files = []
    page_token = None
    fid = _validate_folder_id(folder_id)
    while True:
        resp = _with_retry(
            lambda pt=page_token, f=fid, flds=fields: svc.files().list(
                q=f"'{f}' in parents and trashed=false",
                fields=f"nextPageToken, files({flds})",
                pageToken=pt,
                pageSize=1000,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            ).execute()
        )
        all_files.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return all_files


def _list_children(svc, folder_id: str) -> list:
    """Return list of dicts with id, name, mimeType."""
    return _list_children_raw(svc, folder_id, "id, name, mimeType, size, md5Checksum")


def _list_all_children_with_size(svc, folder_id: str) -> dict:
    """Return {name: {'id': ..., 'size': ..., 'mimeType': ...}} mapping."""
    result = {}
    for f in _list_children_raw(svc, folder_id, "id, name, size, mimeType, md5Checksum"):
        if f.get("mimeType", "").startswith("application/vnd.google-apps.") and f.get("mimeType") != "application/vnd.google-apps.folder":
            continue
        result[f["name"]] = {
            "id": f["id"],
            "size": int(f["size"]) if "size" in f else -1,
            "mimeType": f.get("mimeType", ""),
            "md5Checksum": f.get("md5Checksum"),
        }
    return result


def _upload_file(
    svc, local_path: Path, parent_id: str,
    file_id: Optional[str] = None, needs_search: bool = True,
    max_retries: int = 5,
    show_progress: bool = True,
    _verify_retries: int = 2,
) -> None:
    from googleapiclient.http import MediaFileUpload
    from googleapiclient.errors import HttpError
    fname = local_path.name
    
    if needs_search:
        safe  = _escape_drive_query(fname)
        parent_id = _validate_folder_id(parent_id)
        hits  = _with_retry(
            lambda n=safe, p=parent_id: svc.files().list(
                q=f"name='{n}' and '{p}' in parents and trashed=false",
                fields="files(id)",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            ).execute()
        ).get("files", [])
        if hits:
            file_id = hits[0]["id"]
    
    file_size = local_path.stat().st_size
    chunksize = _optimal_chunksize(file_size)
    
    # ── 修正1: Simple upload の閾値を大幅に下げる ──
    # 5MB以上は全てresumableにすることで、プログレスバーが正確に更新され、
    # ネットワーク断絶時のリトライもチャンク単位で可能になります。
    _SIMPLE_UPLOAD_LIMIT = 5 * 1024 * 1024  # 5 MB
    
    if file_size < _SIMPLE_UPLOAD_LIMIT:
        # Simple upload (< 5MB): バーなしで実行（一瞬で終わるため）
        if file_id:
            response = _with_retry(
                lambda fid=file_id: svc.files().update(
                    fileId=fid,
                    media_body=MediaFileUpload(str(local_path), resumable=False),
                    fields="id",
                    supportsAllDrives=True,
                ).execute(),
                retries=max_retries,
            )
        else:
            parent_id = _validate_folder_id(parent_id)
            response = _with_retry(
                lambda p=parent_id, n=fname: svc.files().create(
                    body={"name": n, "parents": [p]},
                    media_body=MediaFileUpload(str(local_path), resumable=False),
                    fields="id",
                    supportsAllDrives=True,
                ).execute(),
                retries=max_retries,
            )
        log.info("  ☁️  %s (%.1f MB) — uploaded (simple)", fname, file_size / 1e6)
    else:
        # Resumable upload (>= 5MB)
        media = MediaFileUpload(str(local_path), resumable=True, chunksize=chunksize)
        
        if file_id:
            request = svc.files().update(fileId=file_id, media_body=media, fields="id", supportsAllDrives=True)
        else:
            parent_id = _validate_folder_id(parent_id)
            request = svc.files().create(
                body={"name": fname, "parents": [parent_id]},
                media_body=media, fields="id",
                supportsAllDrives=True,
            )

        pbar = None
        if show_progress:
            pbar = tqdm(total=file_size, unit='B', unit_scale=True, desc=f"  ☁️  {fname}", leave=False)
        
        response = None
        consecutive_errors = 0
        restart_count = 0
        MAX_RESTARTS = 3
        while response is None:
            try:
                status, response = request.next_chunk()
                consecutive_errors = 0
                if status and pbar:
                    pbar.update(int(status.resumable_progress) - pbar.n)
            except HttpError as exc:
                code = exc.resp.status
                if code in (403, 429, 500, 502, 503, 504) and consecutive_errors < max_retries:
                    consecutive_errors += 1
                    wait_sec = 2 ** consecutive_errors
                    log.warning("Upload chunk error %s — retry in %.1fs (%d/%d)",
                                code, wait_sec, consecutive_errors, max_retries)
                    time.sleep(wait_sec)
                elif code in (404, 410):
                    restart_count += 1
                    if restart_count > MAX_RESTARTS:
                        log.error("Upload restart limit reached (%d) for %s", MAX_RESTARTS, fname)
                        if pbar: pbar.close()
                        raise IOError(f"Resumable upload failed after {MAX_RESTARTS} restarts (HTTP {code})")
                    log.warning("Resumable session expired (HTTP %s). Restarting upload (%d/%d).",
                                code, restart_count, MAX_RESTARTS)
                    consecutive_errors = 0
                    media = MediaFileUpload(str(local_path), resumable=True, chunksize=chunksize)
                    if file_id:
                        request = svc.files().update(fileId=file_id, media_body=media, fields="id", supportsAllDrives=True)
                    else:
                        request = svc.files().create(
                            body={"name": fname, "parents": [parent_id]},
                            media_body=media, fields="id",
                            supportsAllDrives=True,
                        )
                    if pbar: pbar.n = 0; pbar.refresh()
                else:
                    if pbar: pbar.close()
                    raise
            except (OSError, socket.timeout) as exc:
                if consecutive_errors < max_retries:
                    consecutive_errors += 1
                    wait_sec = 2 ** consecutive_errors
                    log.warning("Upload chunk error %s — retry in %.1fs (%d/%d)",
                                type(exc).__name__, wait_sec, consecutive_errors, max_retries)
                    time.sleep(wait_sec)
                else:
                    if pbar: pbar.close()
                    raise
            except Exception as exc:
                # Handles "Redirected but the response is missing a Location: header"
                # which occurs when chunk size is too large or the session is stale.
                exc_msg = str(exc)
                if "Location" in exc_msg or "redirect" in exc_msg.lower():
                    restart_count += 1
                    if restart_count > MAX_RESTARTS:
                        if pbar: pbar.close()
                        raise IOError(f"Resumable upload failed (redirect error) after {MAX_RESTARTS} restarts: {exc}")
                    # Halve chunk size and restart the session
                    chunksize = max(chunksize // 2, 256 * 1024)
                    log.warning(
                        "Redirect/Location error — restarting upload with smaller chunksize=%dKB (%d/%d): %s",
                        chunksize // 1024, restart_count, MAX_RESTARTS, exc,
                    )
                    time.sleep(2 ** restart_count)
                    consecutive_errors = 0
                    media = MediaFileUpload(str(local_path), resumable=True, chunksize=chunksize)
                    if file_id:
                        request = svc.files().update(fileId=file_id, media_body=media, fields="id", supportsAllDrives=True)
                    else:
                        request = svc.files().create(
                            body={"name": fname, "parents": [parent_id]},
                            media_body=media, fields="id",
                            supportsAllDrives=True,
                        )
                    if pbar: pbar.n = 0; pbar.refresh()
                else:
                    if pbar: pbar.close()
                    raise

        if pbar:
            pbar.n = file_size
            pbar.refresh()
            pbar.close()

    # ── Post-upload verification with auto-retry on mismatch ──
    if response:
        uploaded_id = response.get("id") or file_id
        if uploaded_id:
            remote_meta = _with_retry(
                lambda uid=uploaded_id: svc.files().get(
                    fileId=uid, fields="size, md5Checksum", supportsAllDrives=True
                ).execute()
            )
            remote_size = int(remote_meta.get("size", -1))
            remote_md5 = remote_meta.get("md5Checksum")

            mismatch = False
            local_md5 = None
            if remote_size != file_size:
                mismatch = True
            elif remote_md5:
                local_md5 = _md5_of(local_path)
                if local_md5 != remote_md5:
                    mismatch = True

            if mismatch and _verify_retries > 0:
                log.warning(
                    "Upload mismatch for %s (local=%s, remote=%s). "
                    "Deleting corrupt remote and re-uploading (%d retries left).",
                    fname, file_size, remote_size, _verify_retries,
                )
                # Delete the corrupt remote file before re-upload
                try:
                    _with_retry(
                        lambda uid=uploaded_id: svc.files().delete(fileId=uid, supportsAllDrives=True).execute()
                    )
                except Exception:
                    log.debug("Could not delete corrupt remote %s — will overwrite.", uploaded_id)
                time.sleep(1)
                # Re-upload with decremented retry counter and no existing file_id
                _upload_file(
                    svc, local_path, parent_id,
                    file_id=None, needs_search=True,
                    max_retries=max_retries,
                    show_progress=show_progress,
                    _verify_retries=_verify_retries - 1,
                )
                return

            if remote_size != file_size:
                raise IOError(
                    f"Upload size mismatch for {fname}: "
                    f"local={file_size}, remote={remote_size}"
                )
            if remote_md5:
                if local_md5 is None:
                    local_md5 = _md5_of(local_path)
                if local_md5 != remote_md5:
                    raise IOError(
                        f"Upload MD5 mismatch for {fname}: "
                        f"local={local_md5}, remote={remote_md5}"
                    )


def _push_recursive(
    svc,
    local_path: Path,
    drive_parent_id: str,
    include_patterns: list,
    exclude_patterns: list,
    existing_children: Optional[dict] = None,
    max_workers: int = 4,
    create_top_folder: bool = True,
) -> None:
    """Recursively push with parallel file uploads within each directory."""
    # TODO: parallelize directory traversal for deep trees
    is_dir = local_path.is_dir()
    if not _should_sync(local_path, include_patterns, exclude_patterns, is_dir=is_dir):
        return
        
    if local_path.is_file():
        if existing_children is not None:
            remote_info = existing_children.get(local_path.name)
            if _should_skip_upload(local_path, remote_info):
                log.debug("  ⏭️  Skipping (same size/md5): %s", local_path.name)
                return
            file_id_existing = remote_info["id"] if remote_info else None
            _upload_file(svc, local_path, drive_parent_id, file_id=file_id_existing, needs_search=(file_id_existing is None))
        else:
            _upload_file(svc, local_path, drive_parent_id)
        return

    if create_top_folder:
        sub_id = _get_or_create_folder(svc, local_path.name, drive_parent_id)
    else:
        sub_id = drive_parent_id
        
    sub_children = _list_all_children_with_size(svc, sub_id)
    
    files_to_upload = []
    dirs_to_recurse = []

    for child in sorted(local_path.iterdir()):
        if not _should_sync(child, include_patterns, exclude_patterns, is_dir=child.is_dir()):
            continue
        if child.is_file():
            files_to_upload.append(child)
        elif child.is_dir():
            dirs_to_recurse.append(child)

    if files_to_upload:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {}
            for f in files_to_upload:
                remote_info = sub_children.get(f.name)
                if _should_skip_upload(f, remote_info):
                    log.debug("  ⏭️  Skipping (same size/md5): %s", f.name)
                    continue
                file_id = remote_info["id"] if remote_info else None
                fut = pool.submit(
                    _upload_file, svc, f, sub_id,
                    file_id=file_id, needs_search=False, show_progress=True,
                )
                futures[fut] = f
            
            submitted = len(futures)
            skipped_count = len(files_to_upload) - submitted
            if submitted > 0 or skipped_count > 0:
                log.info("  📁 %s: %d to upload, %d skipped (same size/md5)",
                         local_path.name, submitted, skipped_count)
            
            if futures:
                done_set, not_done = wait(futures, return_when=FIRST_EXCEPTION)
                for fut in done_set:
                    exc = fut.exception()
                    if exc:
                        for nd in not_done:
                            nd.cancel()
                        if hasattr(pool, 'shutdown'):
                            pool.shutdown(wait=False, cancel_futures=True)
                        log.error("Failed to upload %s: %s", futures[fut], exc)
                        raise exc
                # Safely process remaining not_done tasks if FIRST_EXCEPTION returned due to timeout or edge case
                for fut in not_done:
                    try:
                        fut.result()
                    except Exception as exc:
                        log.error("Failed to upload (late): %s", futures.get(fut, "unknown"))
                        raise

    for d in dirs_to_recurse:
        _push_recursive(
            svc, d, sub_id, include_patterns, exclude_patterns,
            existing_children=None,
            max_workers=max_workers,
            create_top_folder=True,
        )


def _find_in_drive(svc, name: str, parent_id: str):
    """Return metadata dict of a file/folder in Drive, or None. Warns on duplicates."""
    safe = _escape_drive_query(name)
    parent_id = _validate_folder_id(parent_id)
    hits = _with_retry(
        lambda n=safe, p=parent_id: svc.files().list(
            q=f"name='{n}' and '{p}' in parents and trashed=false",
            fields="files(id, name, mimeType, size, md5Checksum)",
            pageSize=10,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
    ).get("files", [])
    if not hits:
        return None
    if len(hits) > 1:
        log.warning(
            "Multiple Drive items named '%s' in folder '%s' — using first match.",
            name, parent_id,
        )
    return hits[0]


def _download_file(
    svc, file_id: str, dest_path: Path, *,
    file_size: Optional[int] = None,
    remote_md5: Optional[str] = None,
    max_retries: int = 5,
    show_progress: bool = True,
    force: bool = False
) -> None:
    from googleapiclient.http import MediaIoBaseDownload
    from googleapiclient.errors import HttpError
    
    if file_size is None or remote_md5 is None:
        file_metadata = _with_retry(
            lambda: svc.files().get(fileId=file_id, fields='size, name, md5Checksum', supportsAllDrives=True).execute()
        )
        file_size = file_size or int(file_metadata.get('size', 0)) or None
        file_name = file_metadata.get('name', dest_path.name)
        remote_md5 = remote_md5 or file_metadata.get('md5Checksum')
    else:
        file_name = dest_path.name

    if not force and dest_path.exists() and file_size is not None:
        if dest_path.stat().st_size == file_size:
            if not remote_md5:
                return
            if _md5_of(dest_path) == remote_md5:
                return

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest_path.parent / f".gdrive_{dest_path.name}.tmp"
    
    pbar = None
    if show_progress and file_size:
        pbar = tqdm(total=file_size, unit='B', unit_scale=True, desc=f"  ⬇️  {file_name}", leave=False)
    
    try:
        for attempt in range(max_retries + 1):
            try:
                req = svc.files().get_media(fileId=file_id, supportsAllDrives=True)
                with open(tmp_path, "wb") as fh:
                    if pbar and attempt > 0:
                        pbar.n = 0
                        pbar.refresh()
                        
                    downloader = MediaIoBaseDownload(fh, req, chunksize=_optimal_chunksize(file_size))
                    done = False
                    while not done:
                        status, done = downloader.next_chunk()
                        if status and pbar:
                            pbar.update(int(status.resumable_progress) - pbar.n)
                break
            except (HttpError, OSError, socket.timeout) as exc:
                if attempt < max_retries:
                    wait_sec = 2 ** (attempt + 1)
                    log.warning("Download chunk error %s — restarting from beginning in %.1fs (%d/%d)",
                                exc, wait_sec, attempt + 1, max_retries)
                    time.sleep(wait_sec)
                else:
                    raise

        if file_size is not None and tmp_path.stat().st_size != file_size:
            raise IOError(
                f"Size mismatch after download: expected {file_size}, "
                f"got {tmp_path.stat().st_size}"
            )
        if remote_md5:
            local_md5 = _md5_of(tmp_path)
            if local_md5 != remote_md5:
                raise IOError(
                    f"MD5 mismatch after download: expected {remote_md5}, got {local_md5}"
                )

        tmp_path.replace(dest_path)
        
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    finally:
        if pbar:
            if file_size:
                pbar.n = file_size
                pbar.refresh()
            pbar.close()


def _pull_recursive(svc, drive_item: dict, dest_path: Path, max_workers: int = 4, force: bool = False) -> None:
    """Recursively pull a Drive file/folder to dest_path with parallel file downloads."""
    if drive_item["mimeType"] == "application/vnd.google-apps.folder":
        dest_path.mkdir(parents=True, exist_ok=True)
        children = _list_children(svc, drive_item["id"])
        
        files = [c for c in children if c["mimeType"] != "application/vnd.google-apps.folder"]
        dirs  = [c for c in children if c["mimeType"] == "application/vnd.google-apps.folder"]

        if files:
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {}
                for child in files:
                    fut = pool.submit(
                        _download_file, svc, child["id"], dest_path / child["name"],
                        file_size=int(child["size"]) if "size" in child and int(child["size"]) >= 0 else None,
                        remote_md5=child.get("md5Checksum"),
                        show_progress=True, force=force
                    )
                    futures[fut] = child["name"]
                if futures:
                    done_set, not_done = wait(futures, return_when=FIRST_EXCEPTION)
                    for fut in done_set:
                        exc = fut.exception()
                        if exc:
                            for nd in not_done:
                                nd.cancel()
                            if hasattr(pool, 'shutdown'):
                                pool.shutdown(wait=False, cancel_futures=True)
                            log.error("Failed to download %s: %s", futures[fut], exc)
                            raise exc
                    for fut in not_done:
                        try:
                            fut.result()
                        except Exception as exc:
                            log.error("Failed to download (late): %s", futures.get(fut, "unknown"))
                            raise

        for child in dirs:
            _pull_recursive(svc, child, dest_path / child["name"], max_workers, force)
    else:
        _download_file(svc, drive_item["id"], dest_path, force=force)


# ════════════════════════════════════════════════════════════
#  STORAGE CLASS
# ════════════════════════════════════════════════════════════

class CommonStorage:
    def __init__(
        self,
        enabled: bool = False,
        include_patterns: Optional[list] = None,
        exclude_patterns: Optional[list] = None,
        gdrive_folder_id: Optional[str] = None,
        sa_key_path: Optional[str] = None,
        kaggle_secret: Optional[str] = None,
        project_folder: Optional[str] = None,
        auth_method: Optional[str] = None,
        force_pull: Optional[bool] = None,
        preserve_structure_dirs: Optional[list] = None,
        max_workers: Optional[int] = None,
        http_timeout: int = 300,
    ):
        self.enabled          = enabled
        self.include_patterns = include_patterns or []
        self.exclude_patterns = exclude_patterns or []
        self.gdrive_folder_id = gdrive_folder_id or _DEFAULT_GDRIVE_FOLDER_ID
        self.sa_key_path      = sa_key_path      or _DEFAULT_SA_KEY_PATH
        self.kaggle_secret    = kaggle_secret     or _DEFAULT_KAGGLE_SECRET
        self.project_folder   = project_folder    or _DEFAULT_PROJECT_FOLDER
        self.auth_method      = auth_method       or _DEFAULT_GDRIVE_AUTH_METHOD
        self.force_pull       = force_pull if force_pull is not None else False
        self.preserve_structure_dirs = preserve_structure_dirs or ["weights", "datasets", "packaged", "logs"]
        self.max_workers      = max_workers if max_workers is not None else _DEFAULT_MAX_WORKERS
        self.http_timeout     = http_timeout

        self.base_path    = _build_base_path(self.project_folder)

        self._svc = None

        if not self.enabled:
            log.info(
                "CommonStorage: GDrive sync is disabled. "
                "Only local storage will be used."
            )
        elif not IN_COLAB and self.gdrive_folder_id in ("your_folder_id", ""):
            raise ValueError(
                "GDRIVE_FOLDER_ID is not configured. "
                "Set it via environment variable or constructor argument."
            )

    def _get_svc(self):
        if self._svc is None:
            svc = _build_drive_service(
                self.sa_key_path, self.kaggle_secret, self.auth_method,
                http_timeout=self.http_timeout,
            )
            if svc is None:
                raise RuntimeError(
                    "Drive API service is not available in this environment. "
                    "Colab should use mounted Drive, not the API."
                )
            self._svc = svc
        return self._svc

    def _colab_ready(self) -> bool:
        if IN_COLAB:
            _ensure_colab_mount(self.base_path)
            return True
        return False

    def _smart_sync_colab(self, src: Path, dest: Path) -> tuple:
        if src.is_file():
            dest.parent.mkdir(parents=True, exist_ok=True)
            if _files_match(src, dest):
                return 0, 1
            tmp_path = dest.with_name(f".{dest.name}.{uuid.uuid4().hex[:8]}.tmp")
            shutil.copy2(src, tmp_path)
            tmp_path.replace(dest)
            return 1, 0

        copied = 0
        skipped = 0
        dest.mkdir(parents=True, exist_ok=True)
        
        files = []
        dirs = []
        for item in src.iterdir():
            if not _should_sync(item, self.include_patterns, self.exclude_patterns, is_dir=item.is_dir()):
                continue
            if item.is_dir():
                dirs.append(item)
            else:
                files.append(item)

        def _copy_one(item):
            dest_item = dest / item.name
            if _files_match(item, dest_item):
                return 0, 1
            tmp_path = dest_item.with_name(f".{dest_item.name}.{uuid.uuid4().hex[:8]}.tmp")
            shutil.copy2(item, tmp_path)
            tmp_path.replace(dest_item)
            return 1, 0

        if files:
            if len(files) <= 2:
                for f in files:
                    c, s = _copy_one(f)
                    copied += c
                    skipped += s
            else:
                with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
                    futs = {pool.submit(_copy_one, f): f for f in files}
                    done_set, not_done = wait(futs, return_when=FIRST_EXCEPTION)
                    for fut in done_set:
                        exc = fut.exception()
                        if exc:
                            for nd in not_done:
                                nd.cancel()
                            if hasattr(pool, 'shutdown'):
                                pool.shutdown(wait=False, cancel_futures=True)
                            raise exc
                    for fut in done_set:
                        c, s = fut.result()
                        copied += c
                        skipped += s
                    # safely handle not_done
                    for fut in not_done:
                        c, s = fut.result()
                        copied += c
                        skipped += s

        for d in dirs:
            sub_c, sub_s = self._smart_sync_colab(d, dest / d.name)
            copied += sub_c
            skipped += sub_s

        return copied, skipped

    def _resolve_project_folder_id(self, svc, create_if_missing: bool = False) -> Optional[str]:
        if not self.project_folder:
            return self.gdrive_folder_id

        try:
            meta = _with_retry(
                lambda: svc.files().get(
                    fileId=self.gdrive_folder_id,
                    fields="id, name",
                    supportsAllDrives=True,
                ).execute()
            )
            if meta.get("name") == self.project_folder:
                return self.gdrive_folder_id
        except Exception:
            pass

        if create_if_missing:
            return _get_or_create_folder(svc, self.project_folder, self.gdrive_folder_id)
        else:
            item = _find_in_drive(svc, self.project_folder, self.gdrive_folder_id)
            if item:
                return item["id"]
            return None

    def ensure_local(self, path, force: Optional[bool] = None) -> None:
        if not self.enabled:
            return

        path  = Path(path)
        force = self.force_pull if force is None else force

        if self._colab_ready():
            preserved_dir = next((d for d in self.preserve_structure_dirs if d in path.parts), None)
            if preserved_dir:
                idx = path.parts.index(preserved_dir)
                sub_path = Path(*path.parts[idx:])
                drive_path = self.base_path / sub_path
            else:
                drive_path = self.base_path / path.name
            
            if path.exists() and not force:
                if path.is_dir() and not (path / ".gdrive_fetch_complete").exists():
                    log.warning("[FETCH] ⚠️ Incomplete directory detected. Resuming: %s", path)
                else:
                    log.info("[FETCH] ⏭️ Skipping (already exists): %s", path)
                    return
                
            if not drive_path.exists():
                log.warning("[FETCH] ❌ Source not found in Drive: %s", drive_path)
                return
                
            log.info("[FETCH] ⬇️ Source: %s -> Dest: %s", drive_path, path)
            if drive_path.is_dir():
                if force and path.exists() and path.is_file():
                    path.unlink()
                copied, skipped = self._smart_sync_colab(drive_path, path)
                if force and path.exists():
                    for item in list(path.rglob('*'))[::-1]:
                        if item.name == ".gdrive_fetch_complete": continue
                        rel = item.relative_to(path)
                        if not (drive_path / rel).exists():
                            if item.is_dir() and item.exists(): shutil.rmtree(item)
                            elif item.exists(): item.unlink()
                log.info("[FETCH] Copied: %d, Skipped: %d", copied, skipped)
                (path / ".gdrive_fetch_complete").touch()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                if path.is_dir():
                    shutil.rmtree(path)
                tmp_path = path.with_name(path.name + ".tmp_fetch")
                shutil.copy2(drive_path, tmp_path)
                tmp_path.replace(path)
            log.info("[FETCH] ✅ Ready at %s", path)
            return

        if path.exists() and not force:
            if path.is_dir() and not (path / ".gdrive_fetch_complete").exists():
                log.warning("[FETCH] ⚠️ Incomplete directory detected. Resuming: %s", path)
            else:
                log.info("[FETCH] ⏭️ Skipping (already exists): %s", path)
                return

        svc  = self._get_svc()
        parent_id = self._resolve_project_folder_id(svc, create_if_missing=False)
        
        if not parent_id:
            log.warning("[FETCH] ❌ Project folder '%s' not found in Drive.", self.project_folder)
            return

        drive_display_path = f"GDrive/{self.project_folder}/{path.name}" if self.project_folder else f"GDrive/{path.name}"
        
        preserved_dir = next((d for d in self.preserve_structure_dirs if d in path.parts), None)
        if preserved_dir:
            idx = path.parts.index(preserved_dir)
            sub_parts = path.parts[idx:-1]
            drive_display_path = f"GDrive/{preserved_dir}/{'/'.join(path.parts[idx+1:])}"
            for part in sub_parts:
                item = _find_in_drive(svc, part, parent_id)
                if item:
                    parent_id = item["id"]
                else:
                    log.warning("[FETCH] ❌ Parent folder '%s' not found in Drive.", part)
                    return

        item = _find_in_drive(svc, path.name, parent_id)
        if item is None:
            log.warning("[FETCH] ❌ Source not found in GDrive: %s", drive_display_path)
            return

        log.info("[FETCH] ⬇️ Source: %s -> Dest: %s", drive_display_path, path)
        _pull_recursive(svc, item, path, max_workers=self.max_workers, force=force)
        if path.is_dir():
            (path / ".gdrive_fetch_complete").touch()
        log.info("[FETCH] ✅ Ready at %s", path)

    def sync_to_drive(self, path) -> None:
        if not self.enabled:
            return

        path = Path(path)
        if not path.exists():
            log.warning("[SYNC] ❌ Local path not found: %s", path)
            return

        if not _should_sync(
            path, self.include_patterns, self.exclude_patterns,
            is_dir=path.is_dir(),
        ):
            log.info("[SYNC] ⏭️ Skipping (excluded by patterns): %s", path)
            return

        if self._colab_ready():
            src_str = str(path)
            gdrive_folder = os.environ.get('GDRIVE_FOLDER', 'AI-Drive')
            project_folder = os.environ.get('PROJECT_FOLDER', 'Taigi-finetune')
            
            try:
                rel_path = os.path.relpath(src_str, "/content")
                if rel_path.startswith(".."): rel_path = os.path.basename(src_str)
            except ValueError:
                rel_path = os.path.basename(src_str)
                
            drive_path = Path(f"/content/drive/MyDrive/{gdrive_folder}/{project_folder}") / rel_path
            
            log.info("[SYNC] 🔄 Source: %s -> Dest: %s", path, drive_path)
            
            if path.is_file():
                drive_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_str, drive_path)
            else:
                log.info("🔍 Calculating total files for %s...", path.name)
                total_files = sum(len(files) for r, d, files in os.walk(src_str))
                drive_path.mkdir(parents=True, exist_ok=True)
                
                with tqdm(total=total_files, desc=f"🚀 Copying {path.name}", unit="file", leave=False) as pbar:
                    for root, dirs, files in os.walk(src_str):
                        dest_dir = drive_path / os.path.relpath(root, src_str)
                        dest_dir.mkdir(parents=True, exist_ok=True)
                        for file_ in files:
                            shutil.copy2(os.path.join(root, file_), dest_dir / file_)
                            pbar.update(1)
            
            log.info("[SYNC] ✅ Synced %s to GDrive via shutil", path.name)
            return

        svc = self._get_svc()
        parent_id = self._resolve_project_folder_id(svc, create_if_missing=True)

        drive_display_path = f"GDrive/{self.project_folder}/{path.name}" if self.project_folder else f"GDrive/{path.name}"

        preserved_dir = next((d for d in self.preserve_structure_dirs if d in path.parts), None)
        if preserved_dir:
            idx = path.parts.index(preserved_dir)
            sub_parts = path.parts[idx:-1]
            drive_display_path = f"GDrive/{preserved_dir}/{'/'.join(path.parts[idx+1:])}"
            for part in sub_parts:
                parent_id = _get_or_create_folder(svc, part, parent_id)

        log.info("[SYNC] 🔄 Source: %s -> Dest: %s", path, drive_display_path)
        if path.is_file():
            _upload_file(svc, path, parent_id)
        else:
            if preserved_dir:
                folder_id = parent_id
                create_top = False
            else:
                folder_id = parent_id
                create_top = True

            _push_recursive(
                svc, path, folder_id,
                self.include_patterns, self.exclude_patterns,
                max_workers=self.max_workers,
                create_top_folder=create_top,
            )
        log.info("[SYNC] ✅ Done: %s", path.name)

    def upload(self, path) -> None:
        self.sync_to_drive(path)

    def download(self, name: str, dest, force: bool = True) -> None:
        """
        Download a named item from Drive to a local destination.
        Always overwrites existing local files/directories by default (force=True).

        Note: Unlike `ensure_local()`, `download()` does not respect 
        `preserve_structure_dirs` and will pull directly from the root 
        of `base_path` or `project_folder` down to a flat `dest_path`.

        Parameters
        ----------
        name : str
            Filename/folder name to look up in the Drive folder.
        dest : str | Path
            Local destination path.
        force : bool
            Whether to overwrite the destination if it already exists.
        """
        if not self.enabled:
            return

        dest_path = Path(dest)
        if self._colab_ready():
            drive_path = self.base_path / name
            if not drive_path.exists():
                log.warning("[FETCH] ❌ Source not found in Drive: %s", drive_path)
                return
            if dest_path.exists() and not force:
                if dest_path.is_dir() and not (dest_path / ".gdrive_fetch_complete").exists():
                    log.warning("[FETCH] ⚠️ Incomplete directory detected. Resuming: %s", dest_path)
                else:
                    log.info("[FETCH] ⏭️ Skipping (already exists): %s", dest_path)
                    return
                
            log.info("[FETCH] ⬇️ Source: %s -> Dest: %s", drive_path, dest_path)
            if drive_path.is_dir():
                if force and dest_path.exists() and dest_path.is_file():
                    dest_path.unlink()
                copied, skipped = self._smart_sync_colab(drive_path, dest_path)
                if force and dest_path.exists():
                    for item in list(dest_path.rglob('*'))[::-1]:
                        if item.name == ".gdrive_fetch_complete": continue
                        rel = item.relative_to(dest_path)
                        if not (drive_path / rel).exists():
                            if item.is_dir() and item.exists(): shutil.rmtree(item)
                            elif item.exists(): item.unlink()
                log.info("[FETCH] Copied: %d, Skipped: %d", copied, skipped)
                (dest_path / ".gdrive_fetch_complete").touch()
            else:
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                if dest_path.is_dir():
                    shutil.rmtree(dest_path)
                tmp_path = dest_path.with_name(dest_path.name + ".tmp_fetch")
                shutil.copy2(drive_path, tmp_path)
                tmp_path.replace(dest_path)
            log.info("[FETCH] ✅ Ready at %s", dest_path)
            return

        if dest_path.exists() and not force:
            if dest_path.is_dir() and not (dest_path / ".gdrive_fetch_complete").exists():
                log.warning("[FETCH] ⚠️ Incomplete directory detected. Resuming: %s", dest_path)
            else:
                log.info("[FETCH] ⏭️ Skipping (already exists): %s", dest_path)
                return

        svc  = self._get_svc()
        parent_id = self._resolve_project_folder_id(svc, create_if_missing=False)
        
        if not parent_id:
            log.warning("[FETCH] ❌ Project folder '%s' not found in Drive.", self.project_folder)
            return

        item = _find_in_drive(svc, name, parent_id)
        if item:
            drive_display = f"GDrive/{self.project_folder}/{name}" if self.project_folder else f"GDrive/{name}"
            log.info("[FETCH] ⬇️ Source: %s -> Dest: %s", drive_display, dest_path)
            _pull_recursive(svc, item, dest_path, max_workers=self.max_workers, force=force)
            if dest_path.is_dir():
                (dest_path / ".gdrive_fetch_complete").touch()
            log.info("[FETCH] ✅ Ready at %s", dest_path)
        else:
            log.warning("[FETCH] ❌ Source not found in GDrive: GDrive/%s", name)
