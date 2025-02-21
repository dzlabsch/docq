"""Google drive service."""

import json
import logging as log
import os
from typing import Any, Optional, Union

from google.auth.external_account_authorized_user import Credentials as ExtCredentials
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

CREDENTIALS_KEY = "DOCQ_GOOGLE_APPLICATION_CREDENTIALS"
REDIRECT_URL_KEY = "DOCQ_GOOGLE_AUTH_REDIRECT_URL"
CREDENTIAL_JSON_KEY = "DOCQ_GOOGLE_APPLICATION_CREDENTIALS_JSON"

GOOGLE_APPLICATION_CREDS_PATH = os.environ.get(CREDENTIALS_KEY)
FLOW_REDIRECT_URI = os.environ.get(REDIRECT_URL_KEY)
CREDENTIAL_JSON = os.environ.get(CREDENTIAL_JSON_KEY)

SCOPES = [
    'https://www.googleapis.com/auth/drive.readonly',
    'https://www.googleapis.com/auth/userinfo.email',
    'openid'
]

KEY = "google_drive-API"
VALID_CREDENTIALS = "valid_credentials"
INVALID_CREDENTIALS = "invalid_credentials"
AUTH_WRONG_EMAIL = "auth_wrong_email"
AUTH_URL = "auth_url"
AUTH_ERROR = "auth_error"

CREDENTIALS = Union[Credentials,  ExtCredentials]
CREDENTIAL_FILE_EXISTS = GOOGLE_APPLICATION_CREDS_PATH is not None and os.path.isfile(GOOGLE_APPLICATION_CREDS_PATH)

def _init() -> None:
    """Initialize."""
    if not CREDENTIAL_FILE_EXISTS or not CREDENTIAL_JSON:
        log.info("services.google_drive -- Google application credentials not found. API disabled.")
    if not FLOW_REDIRECT_URI:
        log.info("services.google_drive -- Google auth redirect url not found. API disabled.")


def get_flow() -> InstalledAppFlow:
    """Get Google Drive flow."""
    if CREDENTIAL_FILE_EXISTS:
        flow =  InstalledAppFlow.from_client_secrets_file(
            GOOGLE_APPLICATION_CREDS_PATH, SCOPES
        )
    else:
        flow = InstalledAppFlow.from_client_config(
            CREDENTIAL_JSON, SCOPES
        )
    flow.redirect_uri = FLOW_REDIRECT_URI
    return flow


def get_credentials(creds: Optional[dict]) -> CREDENTIALS:
    """Get credentials from user info."""
    _creds =  Credentials.from_authorized_user_info(creds, SCOPES)
    if _creds.expired and _creds.refresh_token:
        _creds.refresh(Request())
    return _creds


def refresh_credentials(creds: CREDENTIALS) -> CREDENTIALS:
    """Refresh credentials."""
    creds.refresh(Request())
    return creds


def validate_credentials(creds: Optional[str]) -> Optional[dict]:
    """Validate credentials."""
    if not creds:
        return None
    try:
        _creds = get_credentials(json.loads(creds))
        if _creds.valid:
            return json.loads(_creds.to_json())
        return None
    except Exception as e:
        log.error("Failed to validate credentials: %s", e)
        return None


def get_gdrive_authorized_email(creds: CREDENTIALS) -> str:
    """Get user email."""
    service = build('oauth2', 'v2', credentials=creds)
    return service.userinfo().get().execute()['email']


def get_auth_url_params(email: Optional[str] = None, state: Optional[str] = None) -> dict:
    """Get authorization url params."""
    authorization_params = {
        "access_type": "offline",
        "prompt": "consent",
        "state": state if state else "",
    }
    if email:
        authorization_params["login_hint"] = email
    return authorization_params


def list_folders(creds: dict) -> list[dict]:
    """List folders."""
    _creds = get_credentials(creds)
    drive = build('drive', 'v3', credentials=_creds)
    folders = drive.files().list(
        q="mimeType='application/vnd.google-apps.folder'",
        fields="files(id, name, parents, mimeType, modifiedTime)",
    ).execute()
    return folders.get('files', [])


def get_drive_service(creds: dict | str) -> Any:
    """Get drive service."""
    _cred_dict = {}
    _cred_dict = json.loads(creds) if isinstance(creds, str) else creds
    _creds = get_credentials(_cred_dict)
    return build('drive', 'v3', credentials=_creds)


def _export_gdrive_docs(service: Any, file_id: str) -> Any:
    """Export google docs."""
    return service.files().export(fileId=file_id, mimeType="application/pdf")


def download_file(service: Any, file_id: str, file_name: str, mime: str) -> bool:
    """Download file."""
    try:
        if "google-apps" in mime:
            request = _export_gdrive_docs(service, file_id)
            file_name = f"{file_name}.pdf"
        else:
            request = service.files().get_media(fileId=file_id)
        with open(file_name, "wb") as fh:
            downloader, done = MediaIoBaseDownload(fh, request), False
            while done is False:
                status, done = downloader.next_chunk()
                log.debug("Download - %s", f"{file_name}: {str(status.progress() * 100)}%")
        return True
    except Exception as e:
        log.error("Failed to download file: %s", e)
        return False


def get_auth_url(data: dict) -> Optional[dict]:
    """Get auth url for google drive api."""
    try:
        flow = get_flow()
        code = data.get("code", None)
        if code is not None:
            flow.fetch_token(code=code)
            creds = flow.credentials
            return {"credential": creds.to_json()}
        else:
            email = data.get("email", None)
            state = data.get("state", None)
            authorization_params = get_auth_url_params(email, state)
            authorization_url, state = flow.authorization_url(
                **authorization_params,
            )
            return {"auth_url": authorization_url }
    except Exception as e:
        log.error("Failed to get auth url: %s", e)
        return None


def api_enabled() -> bool:
    """Check if google drive API is enabled."""
    return (CREDENTIAL_FILE_EXISTS or CREDENTIAL_JSON is not None) and FLOW_REDIRECT_URI is not None
