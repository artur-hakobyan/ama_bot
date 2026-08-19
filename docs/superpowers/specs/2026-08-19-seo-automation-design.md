# AMAwalls Blog Automation — Design (Stage 1 + Stage 2)

Date: 2026-08-19
Source documents: `docs/source/` (workflow, SEO prompt, keyword sheet)
Status: approved for Stage 1

## Goal

Replace the manual blog workflow with an automated system: the bot selects keywords,
writes SEO-optimised German articles to a strict spec, creates them as unpublished
Shopify drafts, and the operator reviews/approves in Telegram before publication.

## Owner decisions (2026-08-19)

| Question | Decision |
|---|---|
| Article length | **2000 words** (±100) per the SEO prompt — supersedes the 500–900 house style |
| Draft location | Shopify draft + Telegram review; **no Google Docs step** |
| Approval gates | All four: post ideas → images → finished drafts → schedule |
| First pillar | **Akustik im Büro** (110 keywords, highest volumes) |
| Build order | **Stage 1 first** (writing engine, manual trigger), then Stage 2 (weekly automation) |
| Images | Google Drive `_All Mockups` integration (needs service account) |
| Sheet access | Local xlsx for Stage 1; live Sheets API once the service account exists |

## Content spec (from "Prompt for Blogs (Automation)")

**Persona:** architect + business psychologist, decades as copywriter/marketer,
editorial experience at Dezeen and Schöner Wohnen, SEO/AdWords expert.

**Audience:** average-to-above-average income; values quality and creating a warm,
inviting home that impresses guests; dislikes overt showing off, though prestige
matters; design-lovers who don't consider themselves experts.

**Hard style rules (each is a self-check item):**
- Du-Ansprache; direct pronouns, never "man"
- Active voice, not passive
- No conditional constructions ("wenn-dann", "je-desto")
- Present tense, no perfect tense
- Avoid words longer than ~15 letters
- Sentences ≤ 25 words; max 2 commas per sentence; vary sentence length
- No modal verbs ("kann", "kannst", "möchtest") — state things directly
- Verbs over nominalisations
- No filler words ("auch", "gerade", "verhältnismäßig", "ganz")
- Positively connoted adjectives
- Paragraphs ~160 words (±40); split anything over 200
- One H1, then max three distinct heading formats; never two headings in a row;
  max five paragraphs/lists between headings
- Closing "Fazit" + clear CTA (e.g. "Stöbere in unserer Kollektion")
- Length 2000 words (±100)

**Keywords:**
- Focus keyword must appear multiple times and in the title
- Higher-volume keywords used more often than lower-volume ones
- Natural placement, never forced
- Keyword density ≤ 4%
- Prefer the higher-volume of two similar keywords

**Also required:**
- Internal links to other blog articles at fitting points
- Meta description 120–156 characters with the focus keyword in the first sentence

**Obsolete from the source prompt:** `Temperature=0,3` — the Messages API no longer
accepts temperature on current models; style is controlled by the prompt itself.

## Pillar topics (keyword sheet)

`Grundlagen Akustikbilder` (13) · `Akustik im Büro` (109) · `Homeoffice Akustik` (14) ·
`Design & Gestaltung` (26) · `Kaufberatung & Preise` (15) · `Normen & Vorschriften` (10) ·
`Raumakustik allgemein` (108) · `Alternative Lösungen` (15). Master tab `All Keywords`
(274 rows) carries a `Pillar-Topics` column.

Columns: `Keyword`, `Avg. monthly searches`, `Competition` (Hoch/Mittel/Niedrig or
High/Medium/Low), `Competition (indexed value)` 0–100, bid ranges.

**Selection rule:** highest search volume first, then lowest competition index, excluding
keywords already marked used. Numbers appear in both German (`1.234,5`) and English
(`1234.5`) formats — the parser must handle both.

## Topic-aware voice (mismatch resolved)

The existing brand voice (awkward wall spaces, large-format textile prints) does not fit
the acoustics keyword set. The system prompt becomes **topic-aware**: shared brand voice,
audience and style rules, with a pillar-specific framing block — acoustics expertise for
the acoustic pillars, awkward-wall framing for wall-decor pillars.

## Stage 1 — writing engine (build now)

1. `bot/keywords.py` — read the xlsx, list pillars, select next unused keyword,
   track used keywords in SQLite (`keywords_used` table).
2. `bot/claude_client.py` — new `draft_seo_article()` using the full spec above, plus
   `seo_self_check()` returning per-rule pass/fail (word count, sentence length,
   density, modal verbs, filler words, structure).
3. Automatic revision loop: self-check → if failures, revise → re-check (max 2 rounds).
4. Telegram: **"📊 From keyword sheet"** button in the Blog menu → pick pillar →
   shows the next keyword with volume/competition → confirm → writes → Shopify draft →
   preview with existing buttons.
5. Keyword marked used on publish, not on draft.

## Stage 2 — weekly automation (after Stage 1 quality is proven)

Monday scheduler → 3 proposals (title, outline, supporting keywords) → **Gate 1** →
Drive image suggestions → **Gate 2** → write all 3 → self-check → **Gate 3** →
schedule (Tue 06:00–12:00, Wed/Thu 09:00–15:00) → **Gate 4** → publish →
editorial-plan write-back → mark keywords used in both tabs.

Requires: Google service account (Sheets + Drive), APScheduler, editorial plan sheet
structure.

## Out of scope for Stage 1

Google Docs drafting, Drive images, scheduler, editorial plan write-back, WebP
conversion, Trello tasks, Pinterest research.
