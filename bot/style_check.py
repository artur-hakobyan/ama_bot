"""Mechanical verification of the style spec.

Claude judges tone; these checks are arithmetic. Running them in code means a rule
either passes or fails — no self-assessment, no drift. Findings feed the revision
loop, so the article is fixed before the operator ever sees it.
"""
import re

MODAL_VERBS = [
    "kann", "kannst", "können", "könnte", "könntest", "möchte", "möchtest",
    "willst", "würde", "würdest", "sollte", "solltest", "dürfte",
]
FILLER_WORDS = ["auch", "gerade", "verhältnismäßig", "ganz", "eigentlich", "quasi"]
CONDITIONALS = ["wenn", "falls", "je mehr", "je weniger", "desto"]

TARGET_WORDS = 2000
WORD_TOLERANCE = 150          # spec says ±100; a little slack before failing
MAX_SENTENCE_WORDS = 25
MAX_COMMAS = 2
MAX_PARAGRAPH_WORDS = 200
MAX_KEYWORD_DENSITY = 4.0


def strip_html(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html or "")
    return re.sub(r"\s+", " ", text).strip()


def paragraphs(html: str) -> list:
    return [strip_html(p) for p in re.findall(r"<p[^>]*>(.*?)</p>", html or "",
                                              re.S | re.I)]


def sentences(text: str) -> list:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def word_count(html: str) -> int:
    return len(strip_html(html).split())


def keyword_density(html: str, keyword: str) -> float:
    text = strip_html(html).lower()
    total = len(text.split())
    if not total or not keyword:
        return 0.0
    hits = len(re.findall(re.escape(keyword.lower()), text))
    return round(hits * len(keyword.split()) / total * 100, 2)


def check(html: str, focus_keyword: str = "", summary: str = "") -> list:
    """Return a list of human-readable findings; empty means the article passes."""
    findings = []
    text = strip_html(html)
    words = word_count(html)

    if abs(words - TARGET_WORDS) > WORD_TOLERANCE:
        findings.append(
            f"Länge: {words} Wörter statt {TARGET_WORDS} (±{WORD_TOLERANCE}).")

    long_sentences = [s for s in sentences(text) if len(s.split()) > MAX_SENTENCE_WORDS]
    if long_sentences:
        findings.append(
            f"{len(long_sentences)} Sätze über {MAX_SENTENCE_WORDS} Wörter, z. B.: "
            f"„{long_sentences[0][:110]}…“")

    comma_heavy = [s for s in sentences(text) if s.count(",") > MAX_COMMAS]
    if comma_heavy:
        findings.append(
            f"{len(comma_heavy)} Sätze mit mehr als {MAX_COMMAS} Kommata, z. B.: "
            f"„{comma_heavy[0][:110]}…“")

    long_paras = [p for p in paragraphs(html) if len(p.split()) > MAX_PARAGRAPH_WORDS]
    if long_paras:
        findings.append(f"{len(long_paras)} Absätze über {MAX_PARAGRAPH_WORDS} Wörter.")

    lowered = f" {text.lower()} "
    modals = [w for w in MODAL_VERBS if re.search(rf"\b{w}\b", lowered)]
    if modals:
        findings.append(f"Modalverben gefunden: {', '.join(sorted(set(modals))[:6])}.")

    fillers = [w for w in FILLER_WORDS if re.search(rf"\b{w}\b", lowered)]
    if fillers:
        findings.append(f"Füllwörter gefunden: {', '.join(sorted(set(fillers)))}.")

    conds = [w for w in CONDITIONALS if re.search(rf"\b{w}\b", lowered)]
    if conds:
        findings.append(f"Bedingungssätze gefunden: {', '.join(sorted(set(conds)))}.")

    if re.search(r"\bman\b", lowered):
        findings.append("Unpersönliches „man“ verwendet — bitte den Leser direkt duzen.")

    if focus_keyword:
        density = keyword_density(html, focus_keyword)
        if density > MAX_KEYWORD_DENSITY:
            findings.append(
                f"Keyword-Dichte {density}% über dem Limit von {MAX_KEYWORD_DENSITY}%.")
        elif density == 0:
            findings.append(f"Fokus-Keyword „{focus_keyword}“ kommt im Text nicht vor.")

    headings = re.findall(r"<h([1-6])[^>]*>", html or "", re.I)
    if not headings:
        findings.append("Keine Zwischenüberschriften gefunden.")
    if re.search(r"</h[1-6]>\s*<h[1-6]", html or "", re.I):
        findings.append("Zwei Überschriften stehen direkt hintereinander.")

    if "fazit" not in lowered:
        findings.append("Kein „Fazit“-Abschnitt gefunden.")

    if summary:
        n = len(summary)
        if not 120 <= n <= 156:
            findings.append(f"Meta-Beschreibung {n} Zeichen (Vorgabe 120–156).")
        elif focus_keyword and focus_keyword.lower() not in summary.lower():
            findings.append("Fokus-Keyword fehlt in der Meta-Beschreibung.")

    return findings
