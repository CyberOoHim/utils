"""
Common Storage Utility for Google Drive synchronization.
Supports Colab, Kaggle, and Local environments.
"""

import os
import sys
import json
import pickle
import shutil
import io
import time
import fnmatch
from pathlib import Path

# Environment Detection
IN_COLAB = 'google.colab' in sys.modules or 'COLAB_GPU' in os.environ
IN_KAGGLE = 'KAGGLE_KERNEL_RUN_TYPE' in os.environ
IN_LOCAL = not IN_COLAB and not IN_KAGGLE

class CommonStorage:
    def __init__(self, project_folder='taigi_asr_finetuning', gdrive_folder_id='PLACEHOLDER_ID', 
                 sa_key_path='~/service_account.json', kaggle_secret='GDRIVE_SA_KEY', 
                 enabled=True, include_patterns=None, exclude_patterns=None):
        self.project_folder = project_folder
        self.gdrive_folder_id = gdrive_folder_id
        self.sa_key_path = sa_key_path
        self.kaggle_secret = kaggle_secret
        self.drive_service = None
        self.base_path = None
        self.enabled = enabled
        self.include_patterns = include_patterns or []
        self.exclude_patterns = exclude_patterns or []
        
        if self.enabled:
            self._init_environment()
        else:
            print("⚠️ CommonStorage: GDrive sync is disabled. Only local storage will be used.")
            self.base_path = Path(f'./{self.project_folder}').resolve()
            self.base_path.mkdir(parents=True, exist_ok=True)
        
    def _init_environment(self):
        if IN_COLAB:
            from google.colab import drive as _cdrive
            _cdrive.mount('/content/drive', force_remount=False)
            self.base_path = Path(f'/content/drive/MyDrive/{self.project_folder}')
            self.base_path.mkdir(parents=True, exist_ok=True)
            print(f'✅ CommonStorage: Colab mode → {self.base_path}')
        elif IN_KAGGLE:
            try:
                from kaggle_secrets import UserSecretsClient
                from google.oauth2 import service_account
                from googleapiclient.discovery import build
                
                _creds_dict = json.loads(UserSecretsClient().get_secret(self.kaggle_secret))
                _creds = service_account.Credentials.from_service_account_info(
                    _creds_dict,
                    scopes=['https://www.googleapis.com/auth/drive']
                )
                self.drive_service = build('drive', 'v3', credentials=_creds)
                self.base_path = Path('/kaggle/working') / self.project_folder
                self.base_path.mkdir(parents=True, exist_ok=True)
                print(f'✅ CommonStorage: Kaggle mode → {self.base_path}')
            except Exception as e:
                print(f'⚠️ CommonStorage: Kaggle setup failed ({e}). Proceeding without GDrive sync.')
                self.base_path = Path('/kaggle/working') / self.project_folder
                self.base_path.mkdir(parents=True, exist_ok=True)
        else:
            try:
                from google.oauth2 import service_account
                from google.auth.transport.requests import Request
                from googleapiclient.discovery import build
                
                _SCOPES = ['https://www.googleapis.com/auth/drive']
                _TOKEN_FILE = Path('~/.gdrive_token.pickle').expanduser()
                _SA_PATH = Path(self.sa_key_path).expanduser()

                _creds = None
                if _SA_PATH.exists():
                    _creds = service_account.Credentials.from_service_account_file(str(_SA_PATH), scopes=_SCOPES)
                    print('🔑 CommonStorage: Local using service account')
                else:
                    if _TOKEN_FILE.exists():
                        with open(_TOKEN_FILE, 'rb') as _f:
                            _creds = pickle.load(_f)
                    if not _creds or not _creds.valid:
                        if _creds and _creds.expired and _creds.refresh_token:
                            _creds.refresh(Request())
                        else:
                            from google_auth_oauthlib.flow import InstalledAppFlow
                            _creds = InstalledAppFlow.from_client_secrets_file('client_secrets.json', _SCOPES).run_local_server(port=0)
                        with open(_TOKEN_FILE, 'wb') as _f:
                            pickle.dump(_creds, _f)
                    print('🔑 CommonStorage: Local using OAuth2 token')

                self.drive_service = build('drive', 'v3', credentials=_creds)
                self.base_path = Path(f'~/gdrive_local/{self.project_folder}').expanduser()
                self.base_path.mkdir(parents=True, exist_ok=True)
                print(f'✅ CommonStorage: Local mode → {self.base_path}')
            except Exception as e:
                print(f'⚠️ CommonStorage: Local setup failed ({e}). Proceeding without GDrive sync.')
                self.base_path = Path(f'~/gdrive_local/{self.project_folder}').expanduser()
                self.base_path.mkdir(parents=True, exist_ok=True)

    def _should_sync(self, name):
        """Returns True if the file/folder matches inclusion and does NOT match exclusion patterns."""
        for pattern in self.exclude_patterns:
            if fnmatch.fnmatch(name, pattern):
                return False
        if not self.include_patterns:
            return True
        for pattern in self.include_patterns:
            if fnmatch.fnmatch(name, pattern):
                return True
        return False

    def _get_or_create_folder(self, name, parent_id):
        if not self.drive_service: return None
        q = f"name='{name}' and '{parent_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
        existing = self.drive_service.files().list(q=q, fields='files(id)').execute().get('files', [])
        if existing:
            return existing[0]['id']
        folder = self.drive_service.files().create(
            body={'name': name, 'parents': [parent_id], 'mimeType': 'application/vnd.google-apps.folder'},
            fields='id').execute()
        return folder['id']

    def _upload_file(self, local_path, parent_id, retries=3):
        if not self.drive_service: return
        from googleapiclient.http import MediaFileUpload
        local_path = Path(local_path)
        fname = local_path.name
        
        for attempt in range(retries):
            try:
                existing = self.drive_service.files().list(
                    q=f"name='{fname}' and '{parent_id}' in parents and trashed=false",
                    fields='files(id)').execute().get('files', [])
                media = MediaFileUpload(str(local_path), resumable=True)
                if existing:
                    self.drive_service.files().update(fileId=existing[0]['id'], media_body=media).execute()
                else:
                    self.drive_service.files().create(body={'name': fname, 'parents': [parent_id]}, media_body=media, fields='id').execute()
                break
            except Exception as e:
                print(f"⚠️ Upload failed for {fname} (Attempt {attempt+1}/{retries}): {e}")
                time.sleep(2 ** attempt)

    def _push(self, local_path, drive_parent_id):
        if not self.drive_service: return
        local_path = Path(local_path)
        if not self._should_sync(local_path.name):
            return

        if local_path.is_file():
            self._upload_file(local_path, drive_parent_id)
            print(f'  ☁️  {local_path.name}')
        elif local_path.is_dir():
            sub_id = self._get_or_create_folder(local_path.name, drive_parent_id)
            if sub_id:
                for item in sorted(local_path.iterdir()):
                    self._push(item, sub_id)

    def _download_file(self, drive_file_id, dest_path, retries=3):
        if not self.drive_service: return
        from googleapiclient.http import MediaIoBaseDownload
        
        for attempt in range(retries):
            try:
                request = self.drive_service.files().get_media(fileId=drive_file_id)
                with io.FileIO(str(dest_path), 'wb') as fh:
                    downloader = MediaIoBaseDownload(fh, request)
                    done = False
                    while done is False:
                        status, done = downloader.next_chunk()
                        if status:
                            print(f"\r  ⬇️  Downloading {dest_path.name}: {int(status.progress() * 100)}%", end="")
                print(f"\r  ⬇️  Downloaded {dest_path.name}                   ")
                break
            except Exception as e:
                print(f"\n⚠️ Download failed for {dest_path.name} (Attempt {attempt+1}/{retries}): {e}")
                time.sleep(2 ** attempt)

    def _pull(self, drive_name, dest_path, parent_id):
        if not self.drive_service: return
        dest_path = Path(dest_path)
        results = self.drive_service.files().list(
            q=f"name='{drive_name}' and '{parent_id}' in parents and trashed=false",
            fields='files(id, name, mimeType)').execute().get('files', [])
        if not results:
            print(f'❌ "{drive_name}" not found in Drive')
            return
        item = results[0]
        if item['mimeType'] == 'application/vnd.google-apps.folder':
            dest_path.mkdir(parents=True, exist_ok=True)
            children = self.drive_service.files().list(
                q=f"'{item['id']}' in parents and trashed=false",
                fields='files(id, name, mimeType)').execute().get('files', [])
            for child in children:
                self._pull(child['name'], dest_path / child['name'], item['id'])
        else:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            self._download_file(item['id'], dest_path)

    def sync_to_drive(self, path):
        """Uploads/Copies a local file or folder to the project's Drive folder."""
        if not self.enabled:
            return

        src = Path(path)
        if not src.exists():
            print(f'❌ Path not found: {src}')
            return
        
        if not self._should_sync(src.name):
            print(f'⏭️ Skipping {src.name} (matched exclude pattern)')
            return

        if IN_COLAB:
            dest = self.base_path / src.name
            if src.resolve() != dest.resolve():
                if src.is_dir():
                    ignore_func = shutil.ignore_patterns(*self.exclude_patterns) if self.exclude_patterns else None
                    shutil.copytree(src, dest, dirs_exist_ok=True, ignore=ignore_func)
                else:
                    shutil.copy2(src, dest)
            print(f'💾 Synced {src.name} to {dest}')
        elif self.drive_service:
            print(f'🔄 Syncing {src.name} to GDrive...')
            self._push(src, self.gdrive_folder_id)
            print(f'✅ Done syncing {src.name}')

    def sync_from_drive(self, name, dest=None, force=False):
        """Downloads/Copies from Drive to a local destination."""
        dest = Path(dest) if dest else self.base_path / name

        if not self.enabled:
            return dest if dest.exists() else None
        
        if force and dest.exists():
            print(f"🗑️ Force mode: Removing existing local path {dest}")
            if dest.is_dir():
                shutil.rmtree(dest)
            else:
                dest.unlink()

        if IN_COLAB:
            colab_path = self.base_path / name
            if colab_path.exists():
                print(f'📂 Found {colab_path}')
                if dest.resolve() != colab_path.resolve():
                    if colab_path.is_dir():
                        shutil.copytree(colab_path, dest, dirs_exist_ok=True)
                    else:
                        shutil.copy2(colab_path, dest)
                return dest
            else:
                print(f'❌ Not found at {colab_path}')
                return None
        elif self.drive_service:
            print(f'⬇️  Downloading {name} from GDrive to {dest}...')
            self._pull(name, dest, self.gdrive_folder_id)
            if dest.exists():
                print(f'✅ Available at {dest}')
                return dest
            return None
        return dest if dest.exists() else None

    def ensure_local(self, path, drive_name=None, force=False):
        """Checks if a path exists locally. If not, it attempts to pull it from Drive."""
        local_path = Path(path)
        if local_path.exists() and not force:
            return local_path
        
        drive_name = drive_name or local_path.name
        if force:
            print(f"🔄 Force pulling '{drive_name}' from Drive to {local_path}...")
        else:
            print(f"📦 Local path {local_path} not found. Attempting to pull '{drive_name}' from Drive...")
        return self.sync_from_drive(drive_name, dest=local_path, force=force)
