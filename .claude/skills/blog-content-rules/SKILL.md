---
name: blog-content-rules
description: Content rules for AMAwalls blog articles — voice, structure, SEO/keyword strategy derived from the live amawalls.com blog. Use this skill whenever writing, reviewing, or regenerating blog content for AMAwalls, editing the drafting prompts in bot/claude_client.py, adding new content features to the bot, or evaluating whether a generated article is on-brand.
---

# AMAwalls Blog Content Rules

The **runtime source of truth is `SYSTEM_PROMPT` in [bot/claude_client.py]** — the bot enforces these rules on every generated article, and `self_check` validates against them. If a rule changes: update the SYSTEM_PROMPT first, then this skill, so the two never drift.

These rules were derived (2026-07-21) from the three live reference articles the owner designated as the voice/structure standard:
- amawalls.com/blogs/all/warum-grosse-wandbilder-ruhiger-wirken-als-kleine-bilder
- amawalls.com/blogs/all/welche-wandgrosse-auf-airbnb-fotos-am-besten-wirkt
- amawalls.com/blogs/all/art-from-space-ama-walls-welt-von-oben

## Voice

German, **du-form** throughout. Warm, organic, accessible-premium; advisory and concrete. Problem→solution framing everywhere. No Werbesprech, no superlatives, no invented facts or prices, no competitor mentions, never "Jetzt kaufen!".

## Structure (every article)

1. Title = concrete problem or benefit ("Warum …", "Welche …"), no clickbait.
2. Intro: 2–4 sentences making the awkward-wall problem tangible — no product yet.
3. 4–6 `<h2>` sections, several phrased as questions ("Warum …", "Welche …", "So …").
4. Short paragraphs (2–4 sentences); a numbered tips list is welcome; `<strong>` sparse.
5. Second-to-last section: **"Fazit: …"** — core message in 1–2 paragraphs.
6. Last section always: **"Für jede Wand der passende Rahmen"** — custom sizes + interchangeable motifs, soft close (newsletter 10% Willkommensrabatt, or hello@amawalls.com for projects).
7. 500–900 words. Product mentions contextual, max one per section; featured designs in rotation: Silent Jelly, Unberührt, Poppy Seed Explosion.

## SEO / keywords

Exactly **one main keyword per article**, woven naturally into the title, the first paragraph, and one `<h2>`. The summary doubles as meta description: ≤160 chars, contains the keyword. The main keyword is the article's first tag (so used keywords are trackable in Shopify and cannibalization is avoidable — check recent articles' first tags before reusing one).

Keyword clusters (researched 2026-07; refresh periodically):
- **Großformat:** großes Wandbild, XXL Wandbild, Wandbilder XXL Wohnzimmer, großformatige Wandbilder, Wandbild nach Maß
- **Schwierige Wände:** Wandgestaltung Dachschräge, Dachschräge gestalten, schmalen Flur gestalten, Wandgestaltung Flur, Nische gestalten, Wandbild Schlafzimmer, Wand hinter dem Bett gestalten
- **Akustik:** Akustikbild, Akustikpaneel Wohnzimmer, Raumakustik verbessern, Schallabsorber Wohnzimmer
- **Vermietung:** Wandbilder für Ferienwohnungen, Airbnb Einrichtung, Ferienwohnung einrichten, Boutique-Hotel-Look
- **Produkt:** Textil-Wandbild, Wandbild mit wechselbarem Motiv

## When editing the bot

Any change to article generation (prompts, new content features, image alt-text) must preserve: du-form, problem-first, the two closing sections, the one-keyword rule, and the 160-char summary limit. The `self_check` prompt in claude_client.py must keep validating whatever this file requires.
