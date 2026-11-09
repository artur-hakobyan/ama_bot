"""Write the "Used in blog" marks for every keyword already used.

The bot refreshes a keyword's marks whenever an article is written, published
or discarded. Run this once after first deploying keyword rotation, so keywords
spent before then are marked too. Safe to repeat: it rewrites the same text.

Run from repo root:  python scripts/sync_keyword_marks.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from bot.config import Config, ConfigError
from bot.db import Database
from bot.google_drive import GoogleClient
from bot.keywords import LiveKeywordSheet


def main() -> int:
    load_dotenv()
    try:
        cfg = Config.load()
    except ConfigError as e:
        print(f"CONFIG ERROR: {e}")
        return 1
    if not (cfg.google_credentials_path and cfg.keyword_spreadsheet_id):
        print("No live keyword sheet configured — nothing to mark.")
        return 1

    db = Database(cfg.db_path)
    keywords = [r["keyword"] for r in
                db._conn.execute("SELECT DISTINCT keyword FROM keyword_uses")]
    sheet = LiveKeywordSheet(GoogleClient(cfg.google_credentials_path),
                             cfg.keyword_spreadsheet_id)
    rows = sheet.mark_usage(db.keyword_marks(keywords))
    db.log_audit(0, "keyword_sheet_mark", "sync script", "ok",
                 f"{len(keywords)} keywords, {rows} rows")
    db.close()
    print(f"Marked {len(keywords)} keywords in {rows} sheet rows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
