"""Persistent style rules learned from review feedback.

This is the "fine-tuning" loop: corrections the operator makes during review are
saved here and injected into every later drafting prompt, and checked by the
self-check. Rules are plain markdown so they stay readable, editable and
removable — unlike model weights.
"""
import json
from datetime import date
from pathlib import Path

DEFAULT_PATH = Path("house_rules.json")


class HouseRules:
    def __init__(self, path=DEFAULT_PATH):
        self._path = Path(path)
        self._rules = self._load()

    def _load(self) -> list:
        if not self._path.exists():
            return []
        try:
            return json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            return []

    def _save(self):
        self._path.write_text(json.dumps(self._rules, ensure_ascii=False, indent=2))

    def add(self, text: str, source: str = "", text_en: str = "") -> dict:
        # `text` is the German instruction the writer receives; `text_en` is the
        # same rule for the operators, who read the bot in English.
        rule = {"text": text.strip(), "text_en": (text_en or "").strip(),
                "added": date.today().isoformat(), "source": source[:120]}
        self._rules.append(rule)
        self._save()
        return rule

    def set_image_words(self, rule: dict, words: list):
        """Attach room words to a rule so image selection can act on it.

        The rule text is for the writer; these words are for the mockup picker,
        which matches filenames and cannot read prose.
        """
        rule["image_words"] = list(words)
        self._save()

    def remove(self, index: int) -> dict | None:
        """1-based index, matching what /rules shows the operator."""
        if 1 <= index <= len(self._rules):
            rule = self._rules.pop(index - 1)
            self._save()
            return rule
        return None

    def all(self) -> list:
        return list(self._rules)

    def as_prompt_block(self) -> str:
        """Rules for the drafting prompt. Newer rules override the base spec."""
        if not self._rules:
            return ""
        lines = "\n".join(f"- {r['text']}" for r in self._rules)
        return (
            "\n\nZUSÄTZLICHE HAUSREGELN (vom Betreiber im Review festgelegt — "
            "diese haben Vorrang vor den allgemeinen Vorgaben oben, falls sich "
            f"etwas widerspricht):\n{lines}"
        )

    def as_check_block(self) -> str:
        if not self._rules:
            return ""
        lines = "\n".join(f"- {r['text']}" for r in self._rules)
        return f"\n\nZusätzliche Hausregeln, die ebenfalls geprüft werden müssen:\n{lines}"
