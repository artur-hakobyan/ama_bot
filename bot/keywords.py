"""Keyword inventory: read the pillar-topic workbook, pick the next target keyword.

The workbook is the manual research output (Google Ads Keyword Planner export).
Numbers arrive in both German ("1.234,5", "Hoch") and English ("1234.5", "High")
formats depending on which tab they were pasted into, so every parse tolerates both.
"""
import re
from dataclasses import dataclass

from openpyxl import load_workbook

MASTER_TAB = "All Keywords"

# Competition labels, German and English, mapped to a sortable rank.
COMPETITION_RANK = {
    "niedrig": 0, "low": 0,
    "mittel": 1, "medium": 1,
    "hoch": 2, "high": 2,
    "unbekannt": 3, "unknown": 3, "": 3,
}


@dataclass(frozen=True)
class Keyword:
    keyword: str
    volume: int
    competition: str
    competition_index: int
    pillar: str

    @property
    def label(self) -> str:
        vol = f"{self.volume:,}".replace(",", ".") if self.volume else "?"
        return f"{self.keyword} — {vol}/Monat, {self.competition or 'unbekannt'}"


def parse_number(value) -> int:
    """Accept 5000, 5000.0, '5.000', '5000,0', '' -> int (0 when unknown)."""
    if value is None or value == "":
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if not text or text == "--":
        return 0
    # Both conventions appear in this workbook:
    #   German  "5.000" / "1.234,5"  -> "." groups thousands, "," is decimal
    #   English "5000.0"             -> "." is decimal
    if "," in text and "." in text:          # "1.234,5" -> 1234.5
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:                        # "5000,0" -> 5000.0
        text = text.replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", text):   # "5.000" -> 5000 (never 5.0)
        text = text.replace(".", "")
    try:
        return int(float(text))
    except ValueError:
        return 0


def competition_rank(label: str) -> int:
    return COMPETITION_RANK.get((label or "").strip().lower(), 3)


class KeywordSheet:
    def __init__(self, path: str):
        self._wb = load_workbook(path, data_only=True)

    @property
    def pillars(self) -> list:
        """Pillar tabs, excluding the master tab, in workbook (priority) order."""
        return [n for n in self._wb.sheetnames if n != MASTER_TAB]

    def keywords(self, pillar: str) -> list:
        ws = self._wb[pillar]
        rows = ws.iter_rows(values_only=True)
        header = next(rows, None)
        if header is None:
            return []
        cols = {str(h).strip(): i for i, h in enumerate(header) if h}
        i_kw = cols.get("Keyword", 0)
        i_vol = cols.get("Avg. monthly searches")
        i_comp = cols.get("Competition")
        i_idx = cols.get("Competition (indexed value)")
        i_pillar = cols.get("Pillar-Topics")

        out = []
        for row in rows:
            if not row or not row[i_kw]:
                continue
            out.append(Keyword(
                keyword=str(row[i_kw]).strip(),
                volume=parse_number(row[i_vol]) if i_vol is not None else 0,
                competition=str(row[i_comp] or "").strip() if i_comp is not None else "",
                competition_index=parse_number(row[i_idx]) if i_idx is not None else 0,
                pillar=(str(row[i_pillar]).strip()
                        if i_pillar is not None and row[i_pillar] else pillar),
            ))
        return out

    def ranked(self, pillar: str, used: set) -> list:
        """Highest volume first, then lowest competition; used keywords removed."""
        fresh = [k for k in self.keywords(pillar) if k.keyword.lower() not in used]
        return sorted(fresh, key=lambda k: (-k.volume, competition_rank(k.competition),
                                            k.competition_index))

    def next_keyword(self, pillar: str, used: set):
        ranked = self.ranked(pillar, used)
        return ranked[0] if ranked else None

    def remaining(self, pillar: str, used: set) -> int:
        return len(self.ranked(pillar, used))
