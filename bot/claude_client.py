import json

from anthropic import AsyncAnthropic


class ClaudeError(Exception):
    pass


SYSTEM_PROMPT = """Du bist der Content-Autor von AMAwalls, einem Shop für maßgefertigte \
großformatige Textildrucke mit austauschbaren Rahmen sowie Akustikpaneele.

Sprache: Deutsch. Ton: warm, organisch, zugänglich-premium — niemals poliert, \
werblich oder aufdringlich.

Nische: ungewöhnliche, sperrige, unnormierte Wandflächen — schmale Nischen, \
Dachschrägen, Alkoven, Rücksprünge, Flächen zwischen Fenstern, Wände über dem Bett. \
Aufbau immer problem-first: Beginne mit der Herausforderung der schwierigen Wand, \
dann die maßgefertigte Lösung als Auflösung.

Featured Designs in Rotation: Silent Jelly, Unberührt, Poppy Seed Explosion.

Wichtig: Der Text ist ein Shopify-Blogartikel, KEINE Werbung — informativ und \
nützlich, höchstens ein sanfter Call-to-Action am Ende.

Wenn du nach JSON gefragt wirst, antworte NUR mit validem JSON, ohne Erklärtext."""


class ClaudeClient:
    def __init__(self, api_key: str, model: str, client=None):
        self._client = client or AsyncAnthropic(api_key=api_key)
        self._model = model

    async def _ask(self, prompt: str, max_tokens: int = 4096) -> str:
        last_error = None
        for _ in range(2):
            try:
                resp = await self._client.messages.create(
                    model=self._model, max_tokens=max_tokens,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}])
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

Antworte als JSON mit exakt diesen Keys:
{{"title_a": "Titelvariante A (problem-first)",
 "title_b": "Titelvariante B (anderer Blickwinkel)",
 "body_html": "vollständiger Artikel als sauberes HTML (<p>, <h2>, <ul>), 600-900 Wörter",
 "summary": "2-3 Sätze Zusammenfassung",
 "tags": ["3-5", "deutsche", "tags"]}}"""
        draft = self._parse_json(await self._ask(prompt))
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
        prompt = f"""Prüfe diesen Artikelentwurf gegen die Markenrichtlinien \
(deutsch, warm/organisch, problem-first, informativ statt werblich, sanfter CTA).

Titel: {draft.get("title_a")}
Zusammenfassung: {draft.get("summary")}
Artikel: {draft.get("body_html")}

Antworte als JSON: {{"ok": true/false, "issues": ["konkrete Probleme, leer wenn ok"]}}"""
        result = self._parse_json(await self._ask(prompt, max_tokens=1024))
        return {"ok": bool(result.get("ok")), "issues": list(result.get("issues") or [])}

    async def alt_text(self, description: str) -> str:
        prompt = (f"Schreibe einen prägnanten deutschen Alt-Text (max. 125 Zeichen) "
                  f"für dieses Bild: {description}. Antworte nur mit dem Alt-Text.")
        return (await self._ask(prompt, max_tokens=200)).strip()
