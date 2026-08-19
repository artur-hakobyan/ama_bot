import json

from anthropic import AsyncAnthropic


class ClaudeError(Exception):
    pass


SYSTEM_PROMPT = """Du bist der Content-Autor von AMAwalls (amawalls.com), einem Shop für \
maßgefertigte großformatige Textildrucke mit austauschbaren Rahmen sowie Akustikpaneele.

Sprache: Deutsch, durchgehend du-Form. Ton: warm, organisch, zugänglich-premium — \
beratend und konkret, niemals poliert, werblich oder aufdringlich. Kein Werbesprech, \
keine Superlative, keine erfundenen Fakten oder Preise, keine Wettbewerber-Nennungen.

Nische: ungewöhnliche, sperrige, unnormierte Wandflächen — schmale Nischen, \
Dachschrägen, Alkoven, Rücksprünge, Flächen zwischen Fenstern, Wände über dem Bett. \
Aufbau immer problem-first: Beginne mit der Herausforderung der schwierigen Wand, \
dann die maßgefertigte Lösung als Auflösung.

Struktur jedes Artikels (Muster der bestehenden AMAwalls-Blogartikel):
- Titel: konkretes Problem oder Nutzenversprechen ("Warum …", "Welche …"), kein Clickbait.
- Einstieg: 2-4 Sätze, die das Wandproblem greifbar machen — ohne Produkt.
- 4-6 <h2>-Abschnitte; mehrere davon als Frage formuliert ("Warum …", "Welche …", "So …").
- Kurze Absätze (2-4 Sätze), gern eine nummerierte Tipp-Liste, sparsames <strong>.
- Vorletzter Abschnitt: "Fazit: …" mit der Kernaussage in 1-2 Absätzen.
- Letzter Abschnitt immer: <h2>Für jede Wand der passende Rahmen</h2> — kurzer Hinweis \
auf maßgefertigte Formate und wechselbare Motive, sanfter Abschluss (z. B. Newsletter \
mit 10% Willkommensrabatt oder hello@amawalls.com für Projektanfragen). Keine harten \
Kaufaufforderungen wie "Jetzt kaufen!".
- Länge: 500-900 Wörter.

SEO: Jeder Artikel hat GENAU EIN Haupt-Keyword (aus den Keyword-Clustern oder vom \
Thema abgeleitet). Es erscheint natürlich im Titel, im ersten Absatz und in mindestens \
einer <h2>-Überschrift — niemals gestopft. Die Zusammenfassung ist zugleich \
Meta-Description: maximal 160 Zeichen, mit dem Haupt-Keyword.

Keyword-Cluster (Recherche-Stand 2026):
- Großformat: großes Wandbild, XXL Wandbild, Wandbilder XXL Wohnzimmer, \
großformatige Wandbilder, Wandbild nach Maß
- Schwierige Wände: Wandgestaltung Dachschräge, Dachschräge gestalten, schmalen \
Flur gestalten, Wandgestaltung Flur, Nische gestalten, Wandbild Schlafzimmer, \
Wand hinter dem Bett gestalten
- Akustik: Akustikbild, Akustikpaneel Wohnzimmer, Raumakustik verbessern, \
Schallabsorber Wohnzimmer
- Vermietung: Wandbilder für Ferienwohnungen, Airbnb Einrichtung, Ferienwohnung \
einrichten, Boutique-Hotel-Look
- Produkt: Textil-Wandbild, Wandbild mit wechselbarem Motiv

Produkte erwähnst du beiläufig im Fließtext (Kollektionen, maßgefertigte Formate, \
Featured Designs in Rotation: Silent Jelly, Unberührt, Poppy Seed Explosion) — \
maximal eine Produkterwähnung pro Abschnitt. Der Text ist ein Shopify-Blogartikel, \
KEINE Werbung: informativ und nützlich zuerst.

Wenn du nach JSON gefragt wirst, antworte NUR mit validem JSON, ohne Erklärtext."""


DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "title_a": {"type": "string"},
        "title_b": {"type": "string"},
        "body_html": {"type": "string"},
        "summary": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title_a", "title_b", "body_html", "summary", "tags"],
    "additionalProperties": False,
}

CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "ok": {"type": "boolean"},
        "issues": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["ok", "issues"],
    "additionalProperties": False,
}


class ClaudeClient:
    def __init__(self, api_key: str, model: str, client=None):
        # Long articles need a generous timeout; retries cover transient
        # network drops (laptop sleep, wifi switch) rather than failing the flow.
        self._client = client or AsyncAnthropic(
            api_key=api_key, timeout=180.0, max_retries=4)
        self._model = model

    async def _ask(self, prompt: str, max_tokens: int = 4096,
                   output_schema: dict | None = None,
                   system: str | None = None, effort: str | None = None) -> str:
        # output_schema uses the API's structured outputs: the response text is
        # guaranteed to be valid JSON matching the schema (no hand-rolled JSON
        # from the model, which breaks on long HTML strings).
        kwargs = {}
        output_config = {}
        if output_schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": output_schema}
        if effort is not None:
            # A 2000-word article at default effort can spend the whole token
            # budget on thinking and never emit the text; cap it deliberately.
            output_config["effort"] = effort
        if output_config:
            kwargs["output_config"] = output_config
        last_error = None
        for _ in range(2):
            try:
                create = self._client.messages.create
                if max_tokens > 8000 and hasattr(self._client.messages, "stream"):
                    # Large outputs must stream or the request hits the HTTP timeout.
                    async with self._client.messages.stream(
                        model=self._model, max_tokens=max_tokens,
                        system=system or SYSTEM_PROMPT,
                        messages=[{"role": "user", "content": prompt}],
                        **kwargs) as stream:
                        resp = await stream.get_final_message()
                else:
                    resp = await create(
                        model=self._model, max_tokens=max_tokens,
                        system=system or SYSTEM_PROMPT,
                        messages=[{"role": "user", "content": prompt}],
                        **kwargs)
                # Responses can lead with a thinking block; take the text block.
                for block in resp.content:
                    if getattr(block, "type", None) == "text":
                        return block.text
                raise ClaudeError("Claude returned no text block")
            except Exception as e:  # anthropic transport/API errors
                last_error = e
        raise ClaudeError(f"Claude request failed after retry: {last_error}")

    @staticmethod
    def _parse_json(text: str) -> dict:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```")[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        try:
            return json.loads(cleaned.strip())
        except json.JSONDecodeError as e:
            raise ClaudeError(f"Claude returned invalid JSON: {e}")

    async def draft_article(self, topic: str, design: str, must_include: str) -> dict:
        prompt = f"""Schreibe einen Blogartikel.

Thema / schwierige Wandsituation: {topic}
Zu featurendes Design: {design}
Muss enthalten sein: {must_include}

Wähle zuerst das eine Haupt-Keyword für diesen Artikel (passend zum Thema, siehe \
Keyword-Cluster) und baue Titel, ersten Absatz und eine <h2> darauf auf. Halte dich \
exakt an die Artikelstruktur aus deinen Richtlinien (problem-first Einstieg, \
Frage-Überschriften, "Fazit: …", Schlussabschnitt "Für jede Wand der passende Rahmen").

Antworte als JSON mit exakt diesen Keys:
{{"title_a": "Titelvariante A (problem-first, enthält das Haupt-Keyword)",
 "title_b": "Titelvariante B (anderer Blickwinkel)",
 "body_html": "vollständiger Artikel als sauberes HTML (<p>, <h2>, <ol>/<ul>, sparsames <strong>), 500-900 Wörter",
 "summary": "Meta-Description: max. 160 Zeichen, enthält das Haupt-Keyword",
 "tags": ["haupt-keyword als erster tag", "dann", "2-4", "weitere"]}}"""
        draft = self._parse_json(
            await self._ask(prompt, max_tokens=8192, output_schema=DRAFT_SCHEMA))
        missing = {"title_a", "title_b", "body_html", "summary", "tags"} - set(draft)
        if missing:
            raise ClaudeError(f"Draft missing keys: {missing}")
        return draft

    async def revise_article(self, body_html: str, instruction: str) -> str:
        prompt = f"""Überarbeite diesen Artikel nach der Anweisung. Antworte NUR mit dem \
vollständigen überarbeiteten HTML, ohne JSON, ohne Erklärung.

Anweisung: {instruction}

Artikel:
{body_html}"""
        return (await self._ask(prompt)).strip()

    async def self_check(self, draft: dict) -> dict:
        prompt = f"""Prüfe diesen Artikelentwurf gegen deine Richtlinien: deutsch in \
du-Form, warm/organisch, problem-first Einstieg ohne Produkt, 4-6 <h2> (mehrere als \
Frage), "Fazit:"-Abschnitt, Schlussabschnitt "Für jede Wand der passende Rahmen", \
500-900 Wörter, informativ statt werblich (kein "Jetzt kaufen!"), genau ein \
Haupt-Keyword natürlich in Titel + erstem Absatz + einer <h2>, Zusammenfassung \
max. 160 Zeichen mit Keyword, keine erfundenen Fakten/Preise.

Titel: {draft.get("title_a")}
Zusammenfassung: {draft.get("summary")}
Artikel: {draft.get("body_html")}

Antworte als JSON: {{"ok": true/false, "issues": ["konkrete Probleme, leer wenn ok"]}}"""
        result = self._parse_json(
            await self._ask(prompt, max_tokens=1024, output_schema=CHECK_SCHEMA))
        return {"ok": bool(result.get("ok")), "issues": list(result.get("issues") or [])}

    async def alt_text(self, description: str) -> str:
        prompt = (f"Schreibe einen prägnanten deutschen Alt-Text (max. 125 Zeichen) "
                  f"für dieses Bild: {description}. Antworte nur mit dem Alt-Text.")
        return (await self._ask(prompt, max_tokens=200)).strip()


# --- SEO long-form articles (from "Prompt for Blogs (Automation)") -------------

SEO_PERSONA = """Du bist Architekt und hast zusätzlich Wirtschaftspsychologie studiert. \
Du hast Jahrzehnte Erfahrung als Werbetexter und im Marketing und einige Jahre in der \
Redaktion des Architekturmagazins Dezeen sowie beim Schöner Wohnen Magazin gearbeitet. \
Du bist Experte für SEO, AdWords und Vermarktung über Suchmaschinen und KI.

Deine Ziele: Kunden über suchmaschinen- und KI-optimierte Texte auf die Website bringen, \
durch kompetente und interessante Inhalte Vertrauen schaffen, Produkte des Shops \
ansprechend präsentieren.

Deine Zielgruppe: überwiegend durchschnittliche bis überdurchschnittliche Einkommen. \
Ihr ist wichtig, etwas in hoher Qualität zu finden, womit sie eine gemütliche, \
einladende Umgebung schaffen und Menschen begeistern. Sie gibt nicht gerne offensichtlich \
an, Prestige bleibt trotzdem ein Thema. Viele sind Designliebhaber oder würden gerne in \
einem gut designten Zuhause wohnen, kennen sich aber selbst nicht gut aus.

Schreibstil: kompetent, aber klar und leicht verständlich. Der Leser soll sich nicht \
konzentrieren müssen. Gib nicht an, übertreibe nicht, klinge niemals abgehoben oder \
überheblich."""

SEO_STYLE_RULES = """Struktur und Sprache — halte jede Regel ein:
- Du-Ansprache: Duze den Leser. Sprich ihn direkt an, vermeide „man“.
- Aktiv statt Passiv.
- Keine Bedingungssätze („wenn-dann“, „je-desto“).
- Präsens, kein Perfekt.
- Vermeide Wörter mit mehr als 15 Buchstaben.
- Sätze mit maximal 25 Wörtern, maximal zwei Kommata pro Satz, wechselnde Satzlängen.
- Keine Modalverben („kann“, „kannst“, „möchtest“, „willst“) — formuliere direkt.
  Falsch: „Helle Farben können den Raum aufhellen.“ Richtig: „Helle Farben hellen den Raum auf.“
- Verben statt Substantivierungen.
  Falsch: „Der Arzt vollzieht die Durchführung einiger Tests.“ Richtig: „Der Arzt führt Tests durch.“
- Keine Füllwörter („auch“, „gerade“, „verhältnismäßig“, „ganz“).
- Positiv konnotierte Adjektive.
- Absätze rund 160 Wörter (±40), niemals über 200.
- Eine Hauptüberschrift, danach maximal drei unterschiedliche Überschriften-Formate. \
Nie zwei Überschriften direkt hintereinander, maximal fünf Absätze oder Listen zwischen \
zwei Überschriften.
- Lange Aufzählungen als Liste formatieren.
- Schließe mit einem Fazit, das die Kernaussagen zusammenfasst und eine klare \
Handlungsaufforderung enthält.
- Länge: 2000 Wörter (±100)."""


def _seo_system_prompt(pillar: str, house_rules_block: str = "") -> str:
    framing = PILLAR_FRAMING.get(pillar, PILLAR_FRAMING["_default"])
    return (f"{SEO_PERSONA}\n\n{framing}\n\n{SEO_STYLE_RULES}{house_rules_block}\n\n"
            "Wenn du nach JSON gefragt wirst, antworte NUR mit validem JSON.")


PILLAR_FRAMING = {
    "_default": """Der Shop: AMAwalls (amawalls.com) fertigt maßgefertigte großformatige \
Textildrucke mit austauschbaren Rahmen sowie Akustikpaneele. Featured Designs in \
Rotation: Silent Jelly, Unberührt, Poppy Seed Explosion.""",

    "Akustik im Büro": """Thema: Raumakustik in Büros und Großraumbüros. Du kennst die \
Praxis: Lärmpegel, Nachhallzeit, Sprachverständlichkeit, konzentriertes Arbeiten. \
Du erklärst fachlich sauber, aber ohne Fachchinesisch. Der Shop: AMAwalls fertigt \
maßgefertigte Akustikbilder und Akustikpaneele, die Schall absorbieren und dabei wie \
Kunst aussehen — die Lösung für Büros, die weder kahle Schaumstoffplatten noch schlechte \
Akustik wollen.""",

    "Homeoffice Akustik": """Thema: Akustik am Arbeitsplatz zu Hause. Videocalls, \
Konzentration, hallende Räume in Wohnungen. Der Shop: AMAwalls fertigt maßgefertigte \
Akustikbilder, die im Wohnraum als Bild wirken statt als Büroausstattung.""",

    "Grundlagen Akustikbilder": """Thema: Was Akustikbilder sind, wie sie funktionieren, \
woraus sie bestehen und wo sie wirken. Erklärender Grundlagen-Artikel. Der Shop: \
AMAwalls fertigt maßgefertigte Akustikbilder mit eigenem Motiv.""",

    "Raumakustik allgemein": """Thema: Raumakustik allgemein — Nachhall, Absorption, \
Schallschutz in Wohn- und Arbeitsräumen. Der Shop: AMAwalls fertigt maßgefertigte \
Akustikbilder und Akustikpaneele.""",

    "Design & Gestaltung": """Thema: Gestaltung mit Akustikbildern — Motive, Formate, \
Platzierung, Wirkung im Raum. Hier verbindest du Design-Kompetenz mit Akustik. \
Der Shop: AMAwalls fertigt maßgefertigte Akustikbilder und großformatige Textildrucke \
mit austauschbaren Rahmen.""",

    "Kaufberatung & Preise": """Thema: Kaufberatung für Akustikbilder — worauf es ankommt, \
Qualitätsmerkmale, Preisgefüge, Maßanfertigung gegen Standardware. Ehrlich und beratend, \
nie werblich. Der Shop: AMAwalls fertigt maßgefertigte Akustikbilder.""",

    "Normen & Vorschriften": """Thema: Normen und Vorschriften zum Schallschutz im Büro \
(DIN 4109, DGUV, VDI 2569). Du erklärst die Regeln praxisnah für Menschen ohne \
Akustik-Ausbildung. Der Shop: AMAwalls fertigt maßgefertigte Akustikbilder.""",

    "Alternative Lösungen": """Thema: Alternativen zu klassischen Akustikpaneelen — \
Trennwände, Deckensegel, Raumteiler, textile Lösungen. Du vergleichst fair. \
Der Shop: AMAwalls fertigt maßgefertigte Akustikbilder als designorientierte Alternative.""",
}

SEO_ARTICLE_SCHEMA = {
    "type": "object",
    "properties": {
        "title_a": {"type": "string"},
        "title_b": {"type": "string"},
        "body_html": {"type": "string"},
        "summary": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "outline": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title_a", "title_b", "body_html", "summary", "tags", "outline"],
    "additionalProperties": False,
}


class SEOWriter:
    """Long-form SEO articles: draft, mechanically check, revise until clean."""

    MAX_REVISIONS = 2

    def __init__(self, claude: "ClaudeClient", house_rules=None):
        self._claude = claude
        self._rules = house_rules

    def _system(self, pillar: str) -> str:
        block = self._rules.as_prompt_block() if self._rules else ""
        return _seo_system_prompt(pillar, block)

    async def draft(self, focus_keyword: str, pillar: str, supporting: list,
                    must_include: str = "-", internal_links: list = None) -> dict:
        supporting_text = ", ".join(supporting) if supporting else "keine weiteren"
        links_text = ("\n".join(f"- {t}: {u}" for t, u in (internal_links or []))
                      or "keine vorhandenen Beiträge")
        prompt = f"""Schreibe einen SEO-Blogartikel.

Fokus-Keyword: {focus_keyword}
Pillar-Thema: {pillar}
Weitere Keywords (nach Suchvolumen absteigend): {supporting_text}
Muss enthalten sein: {must_include}

Bestehende Blogbeiträge für interne Verlinkung (verlinke 2–4 davon an passenden Stellen \
im Fließtext als <a href="...">):
{links_text}

Vorgehen:
1. Überlege, was die Zielgruppe zu diesem Thema am meisten interessiert.
2. Schreibe den Text mit sauberen inhaltlichen Übergängen, ohne Wiederholungen und \
ohne von Thema zu Thema zu springen. Alles muss sachlich richtig sein.
3. Prüfe dich selbst: Wäre dieser Artikel für dich als recherchierenden Kunden \
interessant? Passe an, bis die Antwort ja ist.

Keyword-Regeln: Das Fokus-Keyword steht im Titel und mehrfach im Text. Keywords mit \
höherem Suchvolumen häufiger verwenden. Natürlich einbetten, nie erzwungen. \
Keyword-Dichte unter 4 %.

Antworte als JSON:
{{"title_a": "Titel mit Fokus-Keyword",
 "title_b": "alternativer Titel mit Fokus-Keyword",
 "outline": ["Gliederungspunkt 1", "..."],
 "body_html": "vollständiger Artikel als HTML (<h2>, <h3>, <p>, <ul>, <a href>), 2000 Wörter",
 "summary": "Meta-Beschreibung, 120–156 Zeichen, Fokus-Keyword im ersten Satz",
 "tags": ["fokus-keyword", "weitere", "tags"]}}"""
        draft = self._claude._parse_json(await self._claude._ask(
            prompt, max_tokens=32000, output_schema=SEO_ARTICLE_SCHEMA,
            system=self._system(pillar), effort="low"))
        missing = set(SEO_ARTICLE_SCHEMA["required"]) - set(draft)
        if missing:
            raise ClaudeError(f"SEO draft missing keys: {missing}")
        return draft

    async def revise(self, draft: dict, findings: list, pillar: str,
                     focus_keyword: str) -> dict:
        issues = "\n".join(f"- {f}" for f in findings)
        prompt = f"""Dein Artikel verletzt folgende Vorgaben. Überarbeite ihn so, dass \
jeder Punkt behoben ist. Ändere nur, was nötig ist — Inhalt, Struktur und Qualität \
bleiben erhalten.

Verstöße:
{issues}

Fokus-Keyword: {focus_keyword}

Aktueller Artikel:
Titel: {draft.get('title_a')}
Meta: {draft.get('summary')}
{draft.get('body_html')}

Antworte als JSON mit denselben Keys wie zuvor (title_a, title_b, outline, body_html, \
summary, tags)."""
        revised = self._claude._parse_json(await self._claude._ask(
            prompt, max_tokens=32000, output_schema=SEO_ARTICLE_SCHEMA,
            system=self._system(pillar), effort="low"))
        return revised

    async def write(self, focus_keyword: str, pillar: str, supporting: list,
                    must_include: str = "-", internal_links=None, on_progress=None):
        """Draft, then revise until the mechanical checks pass (or attempts run out).

        Returns (draft, remaining_findings). Remaining findings are surfaced to the
        operator rather than silently accepted.
        """
        from bot import style_check

        draft = await self.draft(focus_keyword, pillar, supporting, must_include,
                                 internal_links)
        for attempt in range(self.MAX_REVISIONS):
            findings = style_check.check(draft.get("body_html", ""), focus_keyword,
                                         draft.get("summary", ""))
            if not findings:
                return draft, []
            if on_progress:
                await on_progress(attempt + 1, findings)
            draft = await self.revise(draft, findings, pillar, focus_keyword)
        findings = style_check.check(draft.get("body_html", ""), focus_keyword,
                                     draft.get("summary", ""))
        return draft, findings
