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
        self._client = client or AsyncAnthropic(api_key=api_key)
        self._model = model

    async def _ask(self, prompt: str, max_tokens: int = 4096,
                   output_schema: dict | None = None) -> str:
        # output_schema uses the API's structured outputs: the response text is
        # guaranteed to be valid JSON matching the schema (no hand-rolled JSON
        # from the model, which breaks on long HTML strings).
        kwargs = {}
        if output_schema is not None:
            kwargs["output_config"] = {
                "format": {"type": "json_schema", "schema": output_schema}}
        last_error = None
        for _ in range(2):
            try:
                resp = await self._client.messages.create(
                    model=self._model, max_tokens=max_tokens,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}],
                    **kwargs)
                return resp.content[0].text
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
