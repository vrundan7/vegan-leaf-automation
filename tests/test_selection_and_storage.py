import json
import os
import time

import pytest

from src.content_manager import LibraryExhausted, load_library, select_next
from src.storage import LockError, StateError, StateStore


def _hist(*pairs):
    return [{"post_id": pid, "category": cat, "date": f"2026-01-{i + 1:02d}", "media_id": "1",
             "published_at": "x"} for i, (pid, cat) in enumerate(pairs)]


# ------------------------------------------------------------- selection
def test_selection_is_deterministic(library_file):
    posts = load_library(library_file)
    assert select_next(posts, []).id == select_next(posts, []).id == "vl-001"


def test_selection_skips_used_posts(library_file):
    posts = load_library(library_file)
    assert select_next(posts, _hist(("vl-001", "quick_recipes"))).id == "vl-002"


def test_selection_avoids_recent_categories(library_file):
    posts = load_library(library_file)
    # vl-001..005 used; vl-006 is quick_recipes, which is in the recent window, so it's skipped
    hist = _hist(("vl-001", "quick_recipes"), ("vl-002", "food_facts"), ("vl-003", "substitutions"),
                 ("vl-004", "grocery_tips"), ("vl-005", "quick_recipes"))
    chosen = select_next(posts, hist, avoid_recent=3)
    assert chosen.category not in {"grocery_tips", "quick_recipes", "substitutions"}
    assert chosen.id == "vl-007"


def test_selection_falls_back_when_all_unused_share_recent_category(library_file):
    posts = [p for p in load_library(library_file) if p.category == "quick_recipes"]
    hist = _hist((posts[0].id, "quick_recipes"))
    assert select_next(posts, hist).id == posts[1].id


def test_library_exhausted(library_file):
    posts = load_library(library_file)
    with pytest.raises(LibraryExhausted):
        select_next(posts, _hist(*[(p.id, p.category) for p in posts]))


# ------------------------------------------------------------- storage
def test_record_and_reload(state_file):
    s = StateStore(state_file)
    s.record_published(post_id="vl-001", date="2026-10-21", media_id="111", category="quick_recipes",
                       image_url="https://x/y.jpg", container_id="222")
    again = StateStore(state_file)
    assert again.published_ids() == {"vl-001"}
    assert again.published_on("2026-10-21")["media_id"] == "111"
    assert not list(state_file.parent.glob("*.tmp"))  # atomic write cleaned up


def test_record_is_idempotent_for_same_media(state_file):
    s = StateStore(state_file)
    kw = dict(post_id="vl-001", date="2026-10-21", media_id="111", category="c", image_url="u")
    s.record_published(**kw)
    s.record_published(**kw)
    assert len(s.history()) == 1


def test_record_conflicting_media_raises(state_file):
    s = StateStore(state_file)
    s.record_published(post_id="vl-001", date="2026-10-21", media_id="111", category="c", image_url="u")
    with pytest.raises(StateError):
        s.record_published(post_id="vl-001", date="2026-10-22", media_id="999", category="c", image_url="u")


def test_missing_state_fails_safely(tmp_path):
    with pytest.raises(StateError, match="not found"):
        StateStore(tmp_path / "nope.json").load()


@pytest.mark.parametrize("content", ["{corrupt", "[]", '{"version": 2, "published": []}',
                                     '{"version": 1, "published": [{"post_id": "x"}]}',
                                     '{"version": 1, "published": "nope"}'])
def test_corrupt_state_fails_safely(tmp_path, content):
    p = tmp_path / "state.json"
    p.write_text(content)
    with pytest.raises(StateError):
        StateStore(p).load()


def test_duplicate_post_in_state_is_corruption(tmp_path):
    rec = {"post_id": "vl-001", "date": "2026-10-21", "media_id": "1", "published_at": "x"}
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"version": 1, "published": [rec, dict(rec, date="2026-10-22")]}))
    with pytest.raises(StateError, match="twice"):
        StateStore(p).load()


def test_lock_blocks_concurrent_run(state_file):
    s = StateStore(state_file)
    with s.lock():
        with pytest.raises(LockError):
            with StateStore(state_file).lock():
                pass
    with s.lock():  # released afterwards
        pass


def test_stale_lock_is_reclaimed(state_file):
    lock = state_file.with_suffix(".lock")
    lock.write_text("123")
    old = time.time() - 3600
    os.utime(lock, (old, old))
    with StateStore(state_file).lock():
        pass
    assert not lock.exists()
