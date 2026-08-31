"""Google Drive + Sheets access for the automation.

One service account covers both: the live keyword sheet (so the operator's edits
take effect immediately) and the mockup library used for article images.
"""
import io
import re

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

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
    def __init__(self, credentials_path: str):
        creds = service_account.Credentials.from_service_account_file(
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
    room = ("ein modernes Büro mit mehreren Arbeitsplätzen"
            if "büro" in (focus_keyword + pillar).lower()
            else "ein modern eingerichteter Wohnraum")
    return (f"{room}, an der Wand ein großformatiges Akustikbild von ama walls, "
            "natürliches Licht, ruhige Farbpalette, fotorealistisch, "
            "keine Menschen im Bild, Querformat 16:9")
