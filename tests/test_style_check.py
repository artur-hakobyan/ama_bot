"""Mechanical style checks — the rules verified in code, not by a model."""
from bot import style_check


# --- keyword grammar --------------------------------------------------------

def test_noun_stack_keywords_are_detected():
    """Google Ads keywords that are two bare nouns read as broken German."""
    assert style_check.is_noun_stack("absorber büro")
    assert style_check.is_noun_stack("akustikbild wirkung")
    assert style_check.is_noun_stack("akustikbilder wohnzimmer")


def test_adjective_phrases_are_not_noun_stacks():
    """"akustische bilder" is correct German and must be left alone.

    Flagging it produced the advice "use Akustische im Bilder", which is not
    German at all — a false positive here actively damages the article.
    """
    assert not style_check.is_noun_stack("akustische bilder")
    assert not style_check.is_noun_stack("schallschluckendes bild")
    assert not style_check.is_noun_stack("individuelle akustikbilder")
    assert not style_check.is_noun_stack("akustikbild")


def test_a_grammatical_keyword_is_not_reported_as_awkward():
    html = ("<h2>Akustische Bilder</h2><p>Akustische Bilder sind eine Lösung. "
            "Akustische Bilder wirken sofort.</p>")
    findings = style_check.check(html, "akustische bilder")
    assert not any("ungrammatically" in f for f in findings), findings


def test_a_noun_stack_pasted_into_prose_is_still_reported():
    html = "<h2>Test</h2><p>Ein Absorber Büro-Konzept hilft im Alltag.</p>"
    findings = style_check.check(html, "absorber büro")
    assert any("ungrammatically" in f for f in findings), findings
