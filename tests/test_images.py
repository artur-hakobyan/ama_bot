"""Choosing mockups: never the same image twice, and the operator can steer it.

The reviewer was shown three images of which two were identical, and wrote
"please try to always use 3 different images. Also use images with the word
'büro' or 'Homeoffice' in the title" — into a step that read no text at all.
"""
import pytest

from bot.google_drive import score_image, suggest_images, _motif_of
from bot.modules.blog import extract_image_words


def _img(name, path=""):
    return {"id": name, "name": name, "path": path}


def test_the_same_file_in_two_folders_is_offered_once():
    """23 of 116 mockups sit in more than one folder; the picker saw duplicates."""
    images = [_img("11_01 Lageplan_Homeoffice.jpg", "A/"),
              _img("11_01 Lageplan_Homeoffice.jpg", "B/"),
              _img("06_05 Everest_Kanzlei.jpg"),
              _img("02_01 Botanical_Praxis.jpg")]
    picks = suggest_images(images, ["akustische bilder"], limit=3)
    assert len({p["name"] for p in picks}) == len(picks)


def test_three_crops_of_one_artwork_are_not_a_choice():
    """A motif code identifies the artwork; a second crop adds nothing."""
    images = [_img("02_01 Botanical_Praxis.jpg"),
              _img("02_01 Botanical_Kanzlei.jpg"),
              _img("02_01 Botanical_Homeoffice.jpg"),
              _img("06_05 Everest_Kanzlei.jpg"),
              _img("11_01 Lageplan_Homeoffice.jpg")]
    picks = suggest_images(images, ["akustische bilder"], limit=3)
    assert len({_motif_of(p["name"]) for p in picks}) == 3


def test_variety_gives_way_rather_than_returning_too_few():
    """Better a second crop than an empty slot when the library is thin."""
    images = [_img("02_01 Botanical_Praxis.jpg"),
              _img("02_01 Botanical_Kanzlei.jpg")]
    picks = suggest_images(images, ["akustische bilder"], limit=3)
    assert len(picks) == 2


def test_a_preferred_word_outranks_a_keyword_match():
    """The audience is offices, so an office shot beats a better-scoring room."""
    office = score_image("11_01 Lageplan_Homeoffice.jpg", ["akustische bilder"],
                        prefer=["homeoffice"])
    living = score_image("02_01 Akustische Bilder_Wohnzimmer.jpg",
                         ["akustische bilder"], prefer=["homeoffice"])
    assert office > living


def test_preference_changes_which_images_are_offered():
    images = [_img("02_01 Botanical_Wohnzimmer.jpg"),
              _img("06_05 Everest_Schlafzimmer.jpg"),
              _img("11_01 Lageplan_Homeoffice.jpg"),
              _img("14_01 Patina_Buero.jpg")]
    picks = suggest_images(images, ["akustische bilder"], limit=2,
                           prefer=["büro", "buero", "homeoffice"])
    names = " ".join(p["name"].lower() for p in picks)
    assert "homeoffice" in names and "buero" in names


def test_the_reviewers_actual_sentence_is_understood():
    words = extract_image_words(
        'Please try to always use 3 different images. Also use images with the '
        'word "büro" or "Homeoffice" in the title. Remember: this is our target '
        'group.')
    assert "büro" in words and "homeoffice" in words


def test_a_word_no_mockup_carries_is_not_learned():
    """Preferring a word absent from every filename would silently do nothing."""
    assert extract_image_words("use nicer pictures please") == []


def test_scoring_is_unchanged_without_a_preference():
    assert score_image("11_01 Lageplan_Homeoffice.jpg", ["lageplan"]) == \
        score_image("11_01 Lageplan_Homeoffice.jpg", ["lageplan"], prefer=[])


def test_the_preference_persists_and_applies_to_later_articles(tmp_path):
    """"Remember: this is our target group" — it must outlive this one article."""
    from bot.house_rules import HouseRules
    from bot.modules.blog import _image_prefs

    rules = HouseRules(tmp_path / "rules.json")
    rule = rules.add("Bildauswahl: bevorzuge Büro-Mockups.",
                     text_en="Image choice: prefer office mockups.")
    rules.set_image_words(rule, ["büro", "homeoffice"])

    class S:
        pass
    services = S()
    services.rules = HouseRules(tmp_path / "rules.json")   # reload from disk
    assert _image_prefs(services) == ["büro", "homeoffice"]


def test_no_preference_when_none_was_given(tmp_path):
    from bot.house_rules import HouseRules
    from bot.modules.blog import _image_prefs

    class S:
        pass
    services = S()
    services.rules = HouseRules(tmp_path / "none.json")
    assert _image_prefs(services) == []
    services.rules = None
    assert _image_prefs(services) == []
