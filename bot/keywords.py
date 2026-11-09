"""Keyword inventory: read the pillar-topic workbook, pick the next target keyword.

The workbook is the manual research output (Google Ads Keyword Planner export).
Numbers arrive in both German ("1.234,5", "Hoch") and English ("1234.5", "High")
formats depending on which tab they were pasted into, so every parse tolerates both.
"""
import re
from dataclasses import dataclass

from openpyxl import load_workbook

MASTER_TAB = "All Keywords"

# Columns the bot maintains in every tab, so the team can see in the sheet itself
# which article used which keyword. Appended after the Keyword Planner columns.
FOCUS_MARK_COL = "Used in blog (focus)"
SUPPORTING_MARK_COL = "Used in blog (supporting)"

# Supporting keywords offered to the writer per batch, least-used first.
SUPPORTING_POOL = 15
# Below this many pillar keywords, the pool is topped up from the master tab.
SUPPORTING_MIN_PILLAR = 10

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

    # --- rotation ---------------------------------------------------------
    # A keyword's "uses" is how many articles already had it as their focus.
    # Ranking by uses first makes the pillar a cycle: every keyword gets one
    # article before any keyword gets a second, and a fully used pillar simply
    # starts round two from its best keyword instead of running dry.

    def _unique(self, pillar: str) -> list:
        seen, out = set(), []
        for k in self.keywords(pillar):
            key = k.keyword.strip().lower()
            if key and key not in seen:
                seen.add(key)
                out.append(k)
        return out

    def ranked(self, pillar: str, uses, exclude=()) -> list:
        """Least-used first; within a round, highest volume then lowest competition.

        `uses` maps lowercase keyword -> times used (a plain set counts as once);
        `exclude` drops keywords already picked elsewhere in the same batch.
        """
        uses = _as_counts(uses)
        skip = {e.strip().lower() for e in exclude}
        fresh = [k for k in self._unique(pillar) if k.keyword.lower() not in skip]
        return sorted(fresh, key=lambda k: (uses.get(k.keyword.lower(), 0),
                                            -k.volume,
                                            competition_rank(k.competition),
                                            k.competition_index))

    def round_of(self, pillar: str, uses) -> int:
        """1 while some keyword is still unused, 2 once all have one article, …"""
        uses = _as_counts(uses)
        counts = [uses.get(k.keyword.lower(), 0) for k in self._unique(pillar)]
        return min(counts) + 1 if counts else 0

    def remaining(self, pillar: str, uses) -> int:
        """Keywords still waiting for their article in the current round."""
        uses = _as_counts(uses)
        counts = [uses.get(k.keyword.lower(), 0) for k in self._unique(pillar)]
        if not counts:
            return 0
        low = min(counts)
        return sum(1 for c in counts if c == low)

    def next_keyword(self, pillar: str, uses):
        ranked = self.ranked(pillar, uses)
        return ranked[0] if ranked else None

    def supporting_candidates(self, pillar: str, supporting_uses, exclude=(),
                              limit: int = SUPPORTING_POOL) -> list:
        """Sheet keywords to offer as supporting keywords, least-used first.

        Only the least-used slice is offered, so the writer cannot keep reaching
        for the same favourites. A small pillar is topped up from the master tab.
        """
        uses = _as_counts(supporting_uses)
        skip = {e.strip().lower() for e in exclude}

        def order(ks):
            return sorted((k for k in ks if k.keyword.lower() not in skip),
                          key=lambda k: (uses.get(k.keyword.lower(), 0), -k.volume))

        pool = [k.keyword for k in order(self._unique(pillar))]
        if len(pool) < SUPPORTING_MIN_PILLAR and MASTER_TAB in self._all_tabs():
            have = {p.lower() for p in pool}
            pool += [k.keyword for k in order(self._unique(MASTER_TAB))
                     if k.keyword.lower() not in have]
        return pool[:limit]

    def _all_tabs(self) -> list:
        wb = getattr(self, "_wb", None)
        return list(wb.sheetnames) if wb is not None else list(self.pillars)

    def mark_usage(self, marks: dict) -> int:
        """The local workbook is a read-only fallback inside the repo: no marks."""
        return 0


def _as_counts(uses) -> dict:
    if isinstance(uses, dict):
        return {str(k).strip().lower(): int(v) for k, v in uses.items()}
    return {str(k).strip().lower(): 1 for k in (uses or ())}


def column_letter(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA."""
    out = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        out = chr(65 + rem) + out
    return out


class LiveKeywordSheet(KeywordSheet):
    """Keyword inventory read from the live Google Sheet.

    Same interface as the workbook version, so callers are unaffected. Values are
    cached briefly: a Monday run touches every tab, and the sheet rarely changes
    mid-run.
    """

    def __init__(self, google_client, spreadsheet_id: str, cache_seconds: int = 300):
        self._g = google_client
        self._id = spreadsheet_id
        self._cache_seconds = cache_seconds
        self._tabs = None
        self._rows = {}
        self._fetched = {}

    @property
    def pillars(self) -> list:
        return [t for t in self._all_tabs() if t != MASTER_TAB]

    def _all_tabs(self) -> list:
        if self._tabs is None:
            self._tabs = self._g.sheet_tabs(self._id)
        return list(self._tabs)

    def _values(self, pillar: str, fresh: bool = False) -> list:
        import time
        now = time.time()
        if (fresh or pillar not in self._rows
                or now - self._fetched.get(pillar, 0) > self._cache_seconds):
            self._rows[pillar] = self._g.sheet_values(self._id, pillar)
            self._fetched[pillar] = now
        return self._rows[pillar]

    def keywords(self, pillar: str) -> list:
        rows = self._values(pillar)
        if not rows:
            return []
        header = rows[0]
        cols = {str(h).strip(): i for i, h in enumerate(header) if h}
        i_kw = cols.get("Keyword", 0)
        i_vol = cols.get("Avg. monthly searches")
        i_comp = cols.get("Competition")
        i_idx = cols.get("Competition (indexed value)")
        i_pillar = cols.get("Pillar-Topics")

        def cell(row, index):
            if index is None or index >= len(row):
                return ""
            return row[index]

        out = []
        for row in rows[1:]:
            if not row or not cell(row, i_kw):
                continue
            out.append(Keyword(
                keyword=str(cell(row, i_kw)).strip(),
                volume=parse_number(cell(row, i_vol)),
                competition=str(cell(row, i_comp) or "").strip(),
                competition_index=parse_number(cell(row, i_idx)),
                pillar=str(cell(row, i_pillar) or pillar).strip() or pillar,
            ))
        return out

    def mark_usage(self, marks: dict) -> int:
        """Write which articles used each keyword into every tab that lists it.

        `marks` maps lowercase keyword -> {"focus": text, "supporting": text};
        an empty text clears the cell (a discarded draft). The two mark columns
        are created after the last header if missing. Rows are located from a
        fresh read, so a sheet the operator re-sorted a minute ago is still hit
        in the right place. Returns the number of keyword rows written.
        """
        if not marks:
            return 0
        wanted = {k.strip().lower(): v for k, v in marks.items()}
        data, rows_written = [], 0
        for tab in self._all_tabs():
            rows = self._values(tab, fresh=True)
            if not rows:
                continue
            header = [str(h).strip() for h in rows[0]]
            cols = {h: i for i, h in enumerate(header) if h}
            i_kw = cols.get("Keyword", 0)
            hits = [(n, row) for n, row in enumerate(rows[1:], start=2)
                    if len(row) > i_kw
                    and str(row[i_kw]).strip().lower() in wanted]
            if not hits:
                continue
            width = len(header)
            targets = {}
            for name in (FOCUS_MARK_COL, SUPPORTING_MARK_COL):
                if name not in cols:
                    cols[name] = width
                    data.append({"range": f"'{tab}'!{column_letter(width)}1",
                                 "values": [[name]]})
                    width += 1
                targets[name] = cols[name]
            for n, row in hits:
                mark = wanted[str(row[i_kw]).strip().lower()]
                for name, role in ((FOCUS_MARK_COL, "focus"),
                                   (SUPPORTING_MARK_COL, "supporting")):
                    data.append({
                        "range": f"'{tab}'!{column_letter(targets[name])}{n}",
                        "values": [[mark.get(role, "")]]})
                rows_written += 1
        if data:
            self._g.sheet_write(self._id, data)
            self._rows.clear()          # positions and contents just changed
        return rows_written
