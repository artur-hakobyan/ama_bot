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
# Hard-banned fillers: never acceptable.
FILLER_WORDS = ["verhältnismäßig", "ganz", "eigentlich", "quasi"]
# Budgeted: natural German needs these occasionally; only overuse is a finding.
BUDGETED_WORDS = {"auch": 3, "gerade": 2, "wenn": 3, "falls": 2}
CONDITIONALS = ["je mehr", "je weniger", "desto"]

# Owner decision 2026-08-19: 1800–2100 counts as on-spec (the model lands ~1800
# reliably; forcing a true 2000 costs an extra revision round for little gain).
MIN_WORDS = 1200
MAX_WORDS = 1600
MAX_SENTENCE_WORDS = 25
MAX_COMMAS = 2
MAX_PARAGRAPH_WORDS = 200
MAX_KEYWORD_DENSITY = 4.0


def strip_html(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html or "")
    return re.sub(r"\s+", " ", text).strip()


def prose_only(html: str) -> str:
    """Body text with lists removed.

    Sentence-length and comma rules apply to prose. List items are fragments by
    design, and gluing them together produced false 40-word "sentences".
    """
    without_lists = re.sub(r"<(ul|ol)[^>]*>.*?</\1>", " ", html or "", flags=re.S | re.I)
    return strip_html(without_lists)


def paragraphs(html: str) -> list:
    return [strip_html(p) for p in re.findall(r"<p[^>]*>(.*?)</p>", html or "",
                                              re.S | re.I)]


def sentences(text: str) -> list:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def clause_commas(sentence: str) -> int:
    """Commas that join clauses, ignoring enumerations.

    The rule exists to stop convoluted sentences, not to ban lists: German writes
    "Teppich, Vorhängen und Polstermöbeln" perfectly naturally. A comma directly
    before "und"/"oder" — or between short items in a run — reads as enumeration.
    """
    parts = sentence.split(",")
    if len(parts) < 2:
        return 0
    clause_count = 0
    for part in parts[1:]:
        words = part.strip().split()
        # A clause carries a verb-ish tail; enumeration items are short fragments.
        if len(words) >= 4:
            clause_count += 1
    return clause_count


def word_count(html: str) -> int:
    return len(strip_html(html).split())


def keyword_density(html: str, keyword: str) -> float:
    text = strip_html(html).lower()
    total = len(text.split())
    if not total or not keyword:
        return 0.0
    hits = len(re.findall(re.escape(keyword.lower()), text))
    return round(hits * len(keyword.split()) / total * 100, 2)


# Brand facts the reviewer corrected (2026-08-24). Prompt instructions alone let
# one panel reference slip through, so these are verified mechanically.
WRONG_BRAND_SPELLINGS = ["AMAwalls", "Amawalls", "AMA Walls", "AMAWalls"]
OWN_PANEL_CLAIM = re.compile(
    r"\b(unsere|unseren|wir bieten|ama walls[^.]{0,40})\s*[^.]{0,40}Akustikpaneele",
    re.I)


def check(html: str, focus_keyword: str = "", summary: str = "") -> list:
    """Return a list of human-readable findings; empty means the article passes."""
    findings = []
    text = strip_html(html)
    prose = prose_only(html)
    words = word_count(html)

    if not MIN_WORDS <= words <= MAX_WORDS:
        findings.append(
            f"Länge: {words} Wörter (Vorgabe {MIN_WORDS}–{MAX_WORDS}).")

    long_sentences = [s for s in sentences(prose) if len(s.split()) > MAX_SENTENCE_WORDS]
    if long_sentences:
        findings.append(
            f"{len(long_sentences)} Sätze über {MAX_SENTENCE_WORDS} Wörter, z. B.: "
            f"„{long_sentences[0][:110]}…“")

    comma_heavy = [s for s in sentences(prose) if clause_commas(s) > MAX_COMMAS]
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

    for word, budget in BUDGETED_WORDS.items():
        hits = len(re.findall(rf"\b{word}\b", lowered))
        if hits > budget:
            findings.append(
                f"„{word}“ {hits}× verwendet (maximal {budget}× erlaubt).")

    if re.search(r"\bman\b", lowered):
        findings.append("Unpersönliches „man“ verwendet — bitte den Leser direkt duzen.")

    if focus_keyword:
        # Google Ads keywords are ungrammatical noun stacks ("absorber büro").
        # Pasted verbatim into prose they read as broken German — the reviewer
        # flagged eight instances. Catch the stacked/hyphenated forms.
        kw_words = focus_keyword.split()
        if len(kw_words) >= 2:
            stacked = re.escape(" ".join(kw_words))
            awkward = re.findall(rf"\b{stacked}[- ][A-Za-zÄÖÜäöü]+", text, re.I)
            if awkward:
                findings.append(
                    "Keyword grammatisch falsch eingebettet: "
                    f"„{awkward[0]}“ — verwende die natürliche Form "
                    f"(z. B. „{kw_words[0].capitalize()} im {kw_words[-1].capitalize()}“).")

        density = keyword_density(html, focus_keyword)
        if density > MAX_KEYWORD_DENSITY:
            findings.append(
                f"Keyword-Dichte {density}% über dem Limit von {MAX_KEYWORD_DENSITY}%.")
        # Natural inflected forms count as topic coverage: every keyword word must
        # appear, even if never as the exact Google Ads string.
        elif not all(re.search(rf"\b{re.escape(w)}", text, re.I) for w in kw_words):
            findings.append(
                f"Thema „{focus_keyword}“ ist im Text nicht erkennbar — "
                "verwende die natürliche Form mehrfach.")

    headings = re.findall(r"<h([1-6])[^>]*>", html or "", re.I)
    if not headings:
        findings.append("Keine Zwischenüberschriften gefunden.")
    if re.search(r"</h[1-6]>\s*<h[1-6]", html or "", re.I):
        findings.append("Zwei Überschriften stehen direkt hintereinander.")

    for wrong in WRONG_BRAND_SPELLINGS:
        if wrong in text:
            findings.append(
                f"Falsche Schreibweise „{wrong}“ — die Marke heißt immer „ama walls“.")
            break

    panel_claim = OWN_PANEL_CLAIM.search(text)
    if panel_claim:
        findings.append(
            f"„{panel_claim.group(0)[:60]}…“ — ama walls verkauft keine Akustikpaneele, "
            "nur Akustikbilder und Textildrucke.")

    if "fazit" not in lowered:
        findings.append("Kein „Fazit“-Abschnitt gefunden.")

    if summary:
        # The reviewer rejected "Der Artikel erklärt …" openings and ad copy in
        # the excerpt: it should summarise content and invite reading.
        low_sum = summary.lower()
        for phrase in ("der artikel erklärt", "der artikel zeigt", "dieser artikel",
                       "in diesem artikel", "der beitrag erklärt"):
            if phrase in low_sum:
                findings.append(
                    f"Meta-Beschreibung beginnt mit „{phrase}“ — fasse den Inhalt "
                    "direkt zusammen, statt den Artikel zu beschreiben.")
                break
        if "amawalls" in low_sum:
            findings.append("Meta-Beschreibung enthält Werbung für AMAwalls — "
                            "sie soll rein inhaltlich zusammenfassen.")
        n = len(summary)
        if not 120 <= n <= 156:
            findings.append(f"Meta-Beschreibung {n} Zeichen (Vorgabe 120–156).")
        elif focus_keyword and focus_keyword.lower() not in summary.lower():
            findings.append("Fokus-Keyword fehlt in der Meta-Beschreibung.")

    return findings
