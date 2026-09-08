"""Google Drive + Sheets access for the automation.

One service account covers both: the live keyword sheet (so the operator's edits
take effect immediately) and the mockup library used for article images.
"""
import io
import logging
import re

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload, MediaIoBaseDownload

logger = logging.getLogger(__name__)

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


def score_image(filename: str, keywords: list, prefer: list = None) -> int:
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
    # Words the operator asked to favour outweigh a keyword match: the audience
    # is offices, so an office shot beats a better-matching living room.
    for word in (prefer or []):
        if word.lower() in name:
            score += 8
    return score


MIN_USEFUL_SCORE = 5


def suggest_images(images: list, keywords: list, limit: int = 3,
                   prefer: list = None) -> list:
    """Best mockups for an article, best first, never the same image twice.

    Returns [] when nothing scores above the usefulness floor — the caller then
    offers to generate an image instead of attaching a poor match.

    The library keeps the same mockup in several folders, so 23 of 116 names
    appear more than once and the reviewer was offered one image three times.
    Deduplicate by filename, and prefer variety of motif over a marginally
    better score: three shots of one artwork are not a choice.

    `prefer` are words the operator asked to favour ("büro", "homeoffice").
    """
    seen = set()
    scored = []
    for i in images:
        if i["name"] in seen:
            continue
        seen.add(i["name"])
        scored.append((score_image(i["name"], keywords, prefer), i))
    good = [(s, i) for s, i in scored if s >= MIN_USEFUL_SCORE]
    good.sort(key=lambda pair: -pair[0])

    # One image per artwork: the leading "02_01" style code identifies the
    # motif, so a second crop of it adds nothing to the choice.
    picked, motifs = [], set()
    for s, i in good:
        motif = _motif_of(i["name"])
        if motif in motifs:
            continue
        motifs.add(motif)
        picked.append(dict(i, score=s))
        if len(picked) == limit:
            return picked
    # Only if variety could not fill the list, fall back to the next best.
    for s, i in good:
        if len(picked) == limit:
            break
        if not any(p["name"] == i["name"] for p in picked):
            picked.append(dict(i, score=s))
    return picked


MOTIF_CODE = re.compile(r"^(\d{2}_\d{2})")


def _motif_of(filename: str) -> str:
    """The artwork a mockup shows: "02_01 Botanical Beauty…" -> "02_01"."""
    m = MOTIF_CODE.match(filename)
    return m.group(1) if m else filename.lower()


def image_brief(focus_keyword: str, pillar: str) -> str:
    """What to generate when the library has nothing suitable."""
    room = ("a modern office with several workstations"
            if "büro" in (focus_keyword + pillar).lower()
            else "a modern, warmly furnished living room")
    return (f"{room}, a large-format ama walls acoustic picture on the wall, "
            "natural light, calm colour palette, photorealistic, "
            "no people in frame, 16:9 landscape")


class LivePrompt:
    """The operator's brief, read from Drive instead of copied into the code.

    The .docx was transcribed into the prompts by hand once. Everything the
    operator added afterwards was invisible to the bot, and they had no way to
    tell — the file looked like it was in use. Reading it live means an edit in
    Drive reaches the next article.

    Cached briefly: a batch writes three articles and must not re-download the
    document for each pass.
    """

    CACHE_SECONDS = 300

    def __init__(self, google_client: "GoogleClient", file_ids: dict):
        self._drive = google_client._drive
        self._file_ids = {k: v for k, v in file_ids.items() if v}
        self._cache = {}

    def _fetch(self, file_id: str) -> str:
        meta = self._drive.files().get(fileId=file_id,
                                       fields="mimeType").execute()
        if meta["mimeType"] == "application/vnd.google-apps.document":
            raw = self._drive.files().export(fileId=file_id,
                                             mimeType="text/plain").execute()
        else:
            raw = self._drive.files().get_media(fileId=file_id).execute()
            if raw[:2] == b"PK":                    # a .docx is a zip
                raw = _docx_text(raw).encode("utf-8")
        return raw.decode("utf-8", "replace").strip()

    def text(self, name: str) -> str:
        """The document's current text, or "" if it cannot be read.

        Never raises: a Drive hiccup must not stop an article — the code's own
        rules still apply, they are simply not topped up from the document.
        """
        import time
        file_id = self._file_ids.get(name)
        if not file_id:
            return ""
        hit = self._cache.get(name)
        if hit and time.time() - hit[0] < self.CACHE_SECONDS:
            return hit[1]
        try:
            text = self._fetch(file_id)
        except Exception as e:
            logger.warning("Live prompt %r unavailable: %s", name, e)
            return hit[1] if hit else ""
        self._cache[name] = (time.time(), text)
        return text


def _docx_text(blob: bytes) -> str:
    """Plain text from a .docx, without pulling in a document library."""
    import io
    import re
    import xml.sax.saxutils as _sax
    import zipfile
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        xml = z.read("word/document.xml").decode("utf-8", "replace")
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<[^>]+>", "", xml)
    return _sax.unescape(xml)


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
