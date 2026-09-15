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


# --- rotation and exhaustion ------------------------------------------------

def _office(n, motif):
    return _img(f"{motif} Motiv {n}_Homeoffice.jpg")


def test_different_articles_get_different_selections():
    """Every article was handed the same three mockups, forever.

    Filenames rarely contain the keyword, so all eight office mockups tie on the
    same score and sorting by score alone fixed the order permanently.
    """
    images = [_office(n, f"0{n}_01") for n in range(1, 7)]
    a = {p["name"] for p in suggest_images(images, ["akustische bilder"], 3,
                                           prefer=["homeoffice"])}
    b = {p["name"] for p in suggest_images(images, ["akustikbild aufbau"], 3,
                                           prefer=["homeoffice"])}
    assert a != b, "two different articles were offered an identical selection"


def test_the_same_article_is_stable():
    """Rotation must not mean random: re-opening a draft shows the same three."""
    images = [_office(n, f"0{n}_01") for n in range(1, 7)]
    first = [p["name"] for p in suggest_images(images, ["akustische bilder"], 3)]
    again = [p["name"] for p in suggest_images(images, ["akustische bilder"], 3)]
    assert first == again


def test_a_used_image_is_not_offered_again():
    images = [_office(n, f"0{n}_01") for n in range(1, 7)]
    picks = suggest_images(images, ["akustische bilder"], 3)
    used = {picks[0]["id"]}
    later = suggest_images(images, ["akustische bilder"], 3, exclude=used)
    assert all(p["id"] not in used for p in later)


def test_a_higher_score_still_wins_over_rotation():
    """Rotation breaks ties only; it must never outrank a better match."""
    images = [_img("11_01 Lageplan_Homeoffice.jpg"),
              _img("06_02 Zeeland_Wohnzimmer.jpg")]
    picks = suggest_images(images, ["akustische bilder"], 1,
                           prefer=["homeoffice"])
    assert "Homeoffice" in picks[0]["name"]


def test_exhausting_the_library_returns_fewer_rather_than_repeats():
    images = [_office(n, f"0{n}_01") for n in range(1, 4)]
    used = {i["name"] for i in images[:2]}
    picks = suggest_images(images, ["akustische bilder"], 3, exclude=used)
    assert len(picks) == 1
    assert picks[0]["name"] not in used


def test_used_images_are_recorded(tmp_path):
    """The button carries only a position, so the file must be recorded on use."""
    from bot.db import Database

    db = Database(str(tmp_path / "t.db"))
    assert db.used_images() == set()
    db.mark_image_used("fileid1", "11_01 Lageplan_Homeoffice.jpg", "draft1")
    assert db.used_images() == {"fileid1"}
    db.mark_image_used("fileid1", "11_01 Lageplan_Homeoffice.jpg", "draft2")
    assert db.used_images() == {"fileid1"}, "re-marking must not duplicate"
    db.close()
