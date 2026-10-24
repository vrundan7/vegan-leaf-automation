import copy
import json
from argparse import Namespace
from pathlib import Path

import pytest

from src import config, generate_post

ROOT = Path(__file__).resolve().parent.parent


def make_entry(i: int, category: str = "quick_recipes", **over) -> dict:
    e = {
        "id": f"vl-{i:03d}",
        "category": category,
        "headline": f"Test Headline Number {i}",
        "points": ["First short point", "Second short point", "Third short point"],
        "caption": "A short, friendly caption.",
        "call_to_action": "Save this.",
        "hashtags": ["#veganleaf", "#vegan", "#plantbased", "#veganfood", "#easyvegan"],
        "alt_text": "Vegan Leaf graphic with a headline and three points.",
        "source_url": "https://example.org/source" if category in {"food_facts", "myths"} else None,
    }
    e.update(over)
    return e


@pytest.fixture
def entry():
    return make_entry(1)


@pytest.fixture
def library_file(tmp_path):
    cats = ["quick_recipes", "food_facts", "substitutions", "grocery_tips", "questions"]
    entries = [make_entry(i + 1, cats[i % len(cats)]) for i in range(10)]
    p = tmp_path / "posts.json"
    p.write_text(json.dumps({"version": 1, "posts": entries}), encoding="utf-8")
    return p


@pytest.fixture
def state_file(tmp_path):
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"version": 1, "published": []}), encoding="utf-8")
    return p


@pytest.fixture
def isolated(tmp_path, monkeypatch, library_file, state_file):
    """CLI args + env wired to a temp dir; no .env loading, no real credentials."""
    for var in ("IG_USER_ID", "IG_ACCESS_TOKEN", "PUBLISH_ENABLED", "PAGES_BASE_URL", "IG_GRAPH_HOST",
                "IG_API_VERSION", "MAX_CAPTION_HASHTAGS", "GITHUB_OUTPUT", "GITHUB_ACTIONS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(config, "_load_dotenv", lambda *a, **k: None)
    run_dir = tmp_path / ".run"
    monkeypatch.setattr(generate_post, "RUN_DIR", run_dir)
    monkeypatch.setattr(generate_post, "PLAN_FILE", run_dir / "plan.json")

    def args(**over):
        base = dict(validate=False, preview=False, dry_run=False, build=False, publish=False, auto=False,
                    history=False, id=None, all=False, date="2026-10-21", content=str(library_file),
                    state=str(state_file), out=str(tmp_path / "out"), site_dir=str(tmp_path / "site"),
                    verbose=False)
        base.update(over)
        return Namespace(**base)

    return Namespace(args=args, tmp=tmp_path, state=state_file, library=library_file)


@pytest.fixture
def live_env(monkeypatch):
    monkeypatch.setenv("IG_USER_ID", "17841400000000000")
    monkeypatch.setenv("IG_ACCESS_TOKEN", "EAAtesttokenvalue1234567890SECRET")
    monkeypatch.setenv("PUBLISH_ENABLED", "true")
    monkeypatch.setenv("PAGES_BASE_URL", "https://example.github.io/vegan-leaf-automation")
    return "EAAtesttokenvalue1234567890SECRET"


def deep(e):
    return copy.deepcopy(e)
