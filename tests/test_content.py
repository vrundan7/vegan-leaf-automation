import json

import pytest

from src.config import CONTENT_FILE
from src.content_manager import (CATEGORIES, LIMITS, ContentError, build_caption, load_library,
                                 validate_entries)
from tests.conftest import deep, make_entry


def test_real_library_is_valid_and_large_enough():
    posts = load_library(CONTENT_FILE)
    assert len(posts) >= 60
    assert {p.category for p in posts} == set(CATEGORIES)


def test_real_library_factual_posts_have_sources():
    raw = json.loads(CONTENT_FILE.read_text(encoding="utf-8"))["posts"]
    for e in raw:
        if e["category"] in {"food_facts", "myths"} or e.get("factual"):
            assert e["source_url"] and e["source_url"].startswith("https://"), e["id"]


def test_real_library_captions_fit_instagram_limits():
    for p in load_library(CONTENT_FILE):
        cap = build_caption(p, 5)
        assert len(cap) <= 2200
        assert sum(w.startswith("#") for w in cap.split()) <= 5


def test_valid_entry_passes(entry):
    assert validate_entries([entry]) == []


@pytest.mark.parametrize("field", ["id", "headline", "points", "caption", "hashtags", "alt_text",
                                   "call_to_action", "source_url", "category"])
def test_missing_field_reported(entry, field):
    e = deep(entry)
    del e[field]
    assert any("missing fields" in err for err in validate_entries([e]))


def test_duplicate_ids_rejected():
    errs = validate_entries([make_entry(1), make_entry(1, headline="Different headline")])
    assert any("duplicate id" in e for e in errs)


def test_duplicate_headlines_rejected():
    errs = validate_entries([make_entry(1), make_entry(2, headline="Test Headline Number 1")])
    assert any("duplicate headline" in e for e in errs)


@pytest.mark.parametrize("tag", ["vegan", "#Vegan", "#vegan food", "#", "#veg-an"])
def test_invalid_hashtags_rejected(entry, tag):
    entry["hashtags"][0] = tag
    assert any("invalid hashtags" in e for e in validate_entries([entry]))


def test_hashtag_count_bounds(entry):
    entry["hashtags"] = entry["hashtags"][:4]
    assert any("hashtags must be" in e for e in validate_entries([entry]))
    entry["hashtags"] = [f"#tag{i}x" for i in range(9)]
    assert any("hashtags must be" in e for e in validate_entries([entry]))


@pytest.mark.parametrize("field,limit", [("headline", "headline"), ("caption", "caption"),
                                         ("alt_text", "alt_text"), ("call_to_action", "cta")])
def test_overlong_text_rejected(entry, field, limit):
    entry[field] = "x" * (LIMITS[limit] + 1)
    assert any(field in e and "max" in e for e in validate_entries([entry]))


def test_overlong_point_and_point_count(entry):
    entry["points"] = ["x" * (LIMITS["point"] + 1), "ok", "ok"]
    assert any("points[0]" in e for e in validate_entries([entry]))
    entry["points"] = ["a", "b"]
    assert any("points must be" in e for e in validate_entries([entry]))


def test_factual_post_requires_source():
    e = make_entry(1, "food_facts")
    e["source_url"] = None
    assert any("requires source_url" in x for x in validate_entries([e]))
    e2 = make_entry(2, "sustainability", factual=True)
    assert any("requires source_url" in x for x in validate_entries([e2]))


def test_non_https_source_rejected(entry):
    entry["source_url"] = "http://example.org"
    assert any("https" in e for e in validate_entries([entry]))


def test_exaggerated_claims_rejected(entry):
    entry["caption"] = "This smoothie is a miracle detox."
    assert any("banned" in e for e in validate_entries([entry]))


def test_unknown_category_rejected(entry):
    entry["category"] = "memes"
    assert any("unknown category" in e for e in validate_entries([entry]))


def test_load_library_raises_with_all_problems(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"posts": [make_entry(1), make_entry(1)]}))
    with pytest.raises(ContentError, match="duplicate id"):
        load_library(p)


def test_load_library_corrupt_json(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json")
    with pytest.raises(ContentError):
        load_library(p)


def test_caption_includes_cta_source_and_limited_hashtags(library_file):
    post = load_library(library_file)[1]  # food_facts entry with a source
    cap = build_caption(post, 3)
    assert post.call_to_action in cap
    assert "Source: https://example.org/source" in cap
    assert cap.split()[-3:] == list(post.hashtags[:3])
