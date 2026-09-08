"""Google Drive + Sheets access for the automation.

One service account covers both: the live keyword sheet (so the operator's edits
take effect immediately) and the mockup library used for article images.
"""
import io
import re

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload, MediaIoBaseDownload

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]

# Folders that must never supply a published image: Pinterest references are
# other people's photography, the rest are unfinished or retired mockups.
EXCLUDED_FOLDERS = ("_Inspiration", "_Archive", "Please add Frame")

# Room tag lives after the last underscore: "Motiv-Name_Wohnzimmer.png".
ROOM_PATTERN = re.compile(r"_([A-Za-zÄÖÜäöü][A-Za-zÄÖÜäöüß]+)(?:[\s(.\-]|$)")

# Rooms that suit an office/acoustics article, best match first.
OFFICE_ROOMS = ["Homeoffice", "Kanzlei", "Praxis", "Office", "Buero", "Büro"]


class GoogleClient:
    def __init__(self, credentials_path: str, credentials=None):
        creds = credentials or service_account.Credentials.from_service_account_file(
            credentials_path, scopes=SCOPES)
        self._drive = build("drive", "v3", credentials=creds, cache_discovery=False)
        self._sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)

    # --- Drive ---------------------------------------------------------------

    def _list_children(self, folder_id: str):
        items, token = [], None
        while True:
            resp = self._drive.files().list(
                q=f"'{folder_id}' in parents and trashed=false",
                fields="nextPageToken, files(id,name,mimeType,modifiedTime)",
                pageSize=1000, pageToken=token,
                supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
            items.extend(resp.get("files", []))
            token = resp.get("nextPageToken")
            if not token:
                return items

    def list_images(self, folder_id: str, _path: str = "", _depth: int = 0) -> list:
        """Publishable images, recursing past excluded folders."""
        images = []
        for f in self._list_children(folder_id):
            name = f["name"]
            if f["mimeType"] == "application/vnd.google-apps.folder":
                if any(name.startswith(x) for x in EXCLUDED_FOLDERS) or _depth >= 3:
                    continue
                images += self.list_images(f["id"], f"{_path}{name}/", _depth + 1)
            elif f["mimeType"].startswith("image/"):
                # .psd and similar working files are not web-publishable.
                if name.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                    images.append({"id": f["id"], "name": name, "path": _path,
                                   "modified": f.get("modifiedTime", "")})
        return images

    def download(self, file_id: str) -> bytes:
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(
            buf, self._drive.files().get_media(fileId=file_id))
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return buf.getvalue()

    # --- Sheets --------------------------------------------------------------

    def sheet_tabs(self, spreadsheet_id: str) -> list:
        meta = self._sheets.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        return [s["properties"]["title"] for s in meta.get("sheets", [])]

    def sheet_values(self, spreadsheet_id: str, tab: str) -> list:
        resp = self._sheets.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=f"'{tab}'").execute()
        return resp.get("values", [])


def room_of(filename: str) -> str:
    """The room tag encoded in the mockup filename, if any."""
    matches = ROOM_PATTERN.findall(filename)
    ignore = {"leer", "Marked", "Art", "from", "Space", "Wandbild", "final", "neu"}
    for m in reversed(matches):
        if m not in ignore:
            return m
    return ""


def score_image(filename: str, keywords: list) -> int:
    """How well a mockup fits an article, by filename alone (no vision cost)."""
    name = filename.lower()
    score = 0
    for kw in keywords:
        for word in kw.lower().split():
            if len(word) > 3 and word in name:
                score += 2
    room = room_of(filename)
    if room in OFFICE_ROOMS:
        score += 5
    return score


MIN_USEFUL_SCORE = 5


def suggest_images(images: list, keywords: list, limit: int = 3) -> list:
    """Best mockups for an article, best first.

    Returns [] when nothing scores above the usefulness floor — the caller then
    offers to generate an image instead of attaching a poor match.
    """
    scored = [(score_image(i["name"], keywords), i) for i in images]
    good = [(s, i) for s, i in scored if s >= MIN_USEFUL_SCORE]
    good.sort(key=lambda pair: -pair[0])
    return [dict(i, score=s) for s, i in good[:limit]]


def image_brief(focus_keyword: str, pillar: str) -> str:
    """What to generate when the library has nothing suitable."""
    room = ("a modern office with several workstations"
            if "büro" in (focus_keyword + pillar).lower()
            else "a modern, warmly furnished living room")
    return (f"{room}, a large-format ama walls acoustic picture on the wall, "
            "natural light, calm colour palette, photorealistic, "
            "no people in frame, 16:9 landscape")


def user_credentials(client_secret_path: str, token_path: str, scopes=None):
    """Credentials acting as a real Google user, refreshed automatically.

    Service accounts own no Drive storage, so on a personal Gmail account they
    cannot create files at all. Running as the account owner uses that person's
    quota instead. The refresh token is obtained once in a browser and then
    reused; only an explicit revoke or a long gap requires signing in again.
    """
    import json
    import os
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    scopes = scopes or SCOPES
    creds = None
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, scopes)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        flow = InstalledAppFlow.from_client_secrets_file(client_secret_path, scopes)
        # Offline access is what makes the refresh token durable; without it the
        # bot would need a browser again within the hour.
        creds = flow.run_local_server(port=0, access_type="offline",
                                      prompt="consent")
    with open(token_path, "w") as fh:
        fh.write(creds.to_json())
    os.chmod(token_path, 0o600)
    return creds


class DriveQuotaError(RuntimeError):
    """Service accounts own no storage, so they cannot create files in a personal
    My Drive folder — only in a Shared Drive. Raised so callers can degrade
    gracefully instead of failing the article."""


def doc_name(number: int, title: str, when=None) -> str:
    """Naming convention from the reviewer: YY_MM_DD_Blog Number_Blog Title."""
    import datetime
    when = when or datetime.date.today()
    safe = title.replace("/", "-").strip()
    return f"{when:%y_%m_%d}_{number:02d}_{safe}"


def _article_html(title: str, body_html: str, meta: str, focus: str,
                  supporting: list, findings: list) -> bytes:
    """Article plus review context, as the reviewer reads it in Google Docs."""
    extras = ", ".join(supporting or [])
    warn = ("<h3>Please check / Bitte pr&uuml;fen</h3><ul>"
            + "".join(f"<li>{f}</li>" for f in findings) + "</ul>") if findings else ""
    return (f"<html><body>"
            f"<h1>{title}</h1>"
            f"<p><b>Focus Keyword:</b> {focus}<br>"
            f"<b>Additional Keywords:</b> {extras}<br>"
            f"<b>Meta-Description:</b> {meta}</p>"
            f"<hr>{body_html}<hr>{warn}"
            f"</body></html>").encode("utf-8")


FOLDER_MIME = "application/vnd.google-apps.folder"

# Where an article lives at each stage. The operator keeps these folders inside
# the automation folder; "Draft" is singular on Drive, so match it exactly.
DRAFT_PATH = ("Blog", "Draft")
APPROVED_PATH = ("Blog", "Approved")


class GoogleDocs:
    """Creates the review draft as a Google Doc, and moves it on approval.

    An article starts in Blog/Draft and moves to Blog/Approved when the operator
    publishes it, so the folder itself says which articles still need review.
    """

    def __init__(self, google_client: "GoogleClient", folder_id: str):
        self._drive = google_client._drive
        self._folder = folder_id
        self._folder_cache = {}

    def _subfolder(self, path: tuple) -> str:
        """Resolve (and create if absent) a folder path under the automation root."""
        if path in self._folder_cache:
            return self._folder_cache[path]
        parent = self._folder
        for name in path:
            safe = name.replace("'", "\\'")
            found = self._drive.files().list(
                q=(f"'{parent}' in parents and name = '{safe}' and "
                   f"mimeType = '{FOLDER_MIME}' and trashed = false"),
                fields="files(id)", pageSize=1,
                supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
            files = found.get("files", [])
            if files:
                parent = files[0]["id"]
            else:
                created = self._drive.files().create(
                    body={"name": name, "parents": [parent],
                          "mimeType": FOLDER_MIME},
                    fields="id", supportsAllDrives=True).execute()
                parent = created["id"]
        self._folder_cache[path] = parent
        return parent

    def move_to_approved(self, file_id: str) -> str:
        """Move an approved article out of Draft. Returns the new parent id."""
        target = self._subfolder(APPROVED_PATH)
        current = self._drive.files().get(
            fileId=file_id, fields="parents", supportsAllDrives=True).execute()
        self._drive.files().update(
            fileId=file_id, addParents=target,
            removeParents=",".join(current.get("parents", [])),
            fields="id,parents", supportsAllDrives=True).execute()
        return target

    def create_draft(self, number: int, title: str, body_html: str, meta: str,
                     focus: str, supporting: list, findings: list) -> dict:
        name = doc_name(number, title)
        try:
            return self._drive.files().create(
                body={"name": name, "parents": [self._subfolder(DRAFT_PATH)],
                      "mimeType": "application/vnd.google-apps.document"},
                media_body=MediaInMemoryUpload(
                    _article_html(title, body_html, meta, focus, supporting,
                                  findings),
                    mimetype="text/html"),
                fields="id,name,webViewLink", supportsAllDrives=True).execute()
        except Exception as e:
            if "storage quota" in str(e).lower():
                raise DriveQuotaError(
                    "The automation folder is in a personal My Drive. Service "
                    "accounts have no storage there — convert it to a Shared "
                    "Drive to enable Google Doc drafts.") from e
            raise
