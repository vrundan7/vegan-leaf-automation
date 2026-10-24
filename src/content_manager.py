"""Content library loading, validation, deterministic selection and caption assembly."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

CATEGORIES = {
    "food_facts": "VEGAN FOOD FACT",
    "ingredients": "INGREDIENT GUIDE",
    "quick_recipes": "QUICK RECIPE",
    "substitutions": "EASY SWAP",
    "grocery_tips": "SHOPPING TIP",
    "beginner_guides": "BEGINNER GUIDE",
    "myths": "MYTH VS FACT",
    "sustainability": "SUSTAINABLE EATING",
    "meal_planning": "MEAL PLANNING",
    "questions": "YOUR TURN",
}
# Categories that make factual claims always need a source; other entries can opt in via "factual": true.
SOURCE_REQUIRED = {"food_facts", "myths"}

LIMITS = {
    "headline": 60,
    "point": 95,
    "points_min": 3,
    "points_max": 5,
    "caption": 1500,
    "cta": 150,
    "alt_text": 1000,   # Instagram Graph API alt_text limit
    "hashtags_min": 5,
    "hashtags_max": 8,
    "full_caption": 2200,  # Instagram caption limit
}
HASHTAG_RE = re.compile(r"^#[a-z0-9_]{2,40}$")
ID_RE = re.compile(r"^vl-\d{3,}$")
URL_RE = re.compile(r"^https://[^\s]+$")
BANNED = ["cure", "cures", "detox", "miracle", "guaranteed", "superfood", "lose weight", "burn fat"]
REQUIRED = ["id", "category", "headline", "points", "caption", "call_to_action", "hashtags", "alt_text", "source_url"]


class ContentError(ValueError):
    pass


class LibraryExhausted(RuntimeError):
    pass


@dataclass(frozen=True)
class Post:
    id: str
    category: str
    headline: str
    points: tuple[str, ...]
    caption: str
    call_to_action: str
    hashtags: tuple[str, ...]
    alt_text: str
    source_url: str | None
    template: str | None = None

    @property
    def label(self) -> str:
        return CATEGORIES[self.category]


def validate_entries(entries: list) -> list[str]:
    """Return a list of human-readable problems (empty list = valid)."""
    errors: list[str] = []
    if not isinstance(entries, list) or not entries:
        return ["library must contain a non-empty 'posts' list"]
    ids: set[str] = set()
    headlines: set[str] = set()
    for i, e in enumerate(entries):
        where = f"posts[{i}]" + (f" ({e.get('id')})" if isinstance(e, dict) and e.get("id") else "")
        if not isinstance(e, dict):
            errors.append(f"{where}: not an object")
            continue
        missing = [k for k in REQUIRED if k not in e]
        if missing:
            errors.append(f"{where}: missing fields {missing}")
            continue
        pid = e["id"]
        if not isinstance(pid, str) or not ID_RE.match(pid):
            errors.append(f"{where}: id must look like vl-001")
        elif pid in ids:
            errors.append(f"{where}: duplicate id {pid}")
        ids.add(pid)
        if e["category"] not in CATEGORIES:
            errors.append(f"{where}: unknown category {e['category']!r}")

        for key, lim in (("headline", "headline"), ("caption", "caption"), ("call_to_action", "cta"), ("alt_text", "alt_text")):
            v = e[key]
            if not isinstance(v, str) or not v.strip():
                errors.append(f"{where}: {key} must be a non-empty string")
            elif len(v) > LIMITS[lim]:
                errors.append(f"{where}: {key} is {len(v)} chars (max {LIMITS[lim]})")
        if isinstance(e["headline"], str):
            h = e["headline"].strip().lower()
            if h in headlines:
                errors.append(f"{where}: duplicate headline")
            headlines.add(h)

        pts = e["points"]
        if not isinstance(pts, list) or not LIMITS["points_min"] <= len(pts) <= LIMITS["points_max"]:
            errors.append(f"{where}: points must be a list of {LIMITS['points_min']}-{LIMITS['points_max']} items")
        else:
            for j, p in enumerate(pts):
                if not isinstance(p, str) or not p.strip():
                    errors.append(f"{where}: points[{j}] empty")
                elif len(p) > LIMITS["point"]:
                    errors.append(f"{where}: points[{j}] is {len(p)} chars (max {LIMITS['point']})")

        tags = e["hashtags"]
        if not isinstance(tags, list) or not LIMITS["hashtags_min"] <= len(tags) <= LIMITS["hashtags_max"]:
            errors.append(f"{where}: hashtags must be a list of {LIMITS['hashtags_min']}-{LIMITS['hashtags_max']}")
        else:
            bad = [t for t in tags if not isinstance(t, str) or not HASHTAG_RE.match(t)]
            if bad:
                errors.append(f"{where}: invalid hashtags {bad} (use lowercase #letters_digits)")
            if len(set(tags)) != len(tags):
                errors.append(f"{where}: duplicate hashtags")

        src = e["source_url"]
        needs_src = e.get("category") in SOURCE_REQUIRED or e.get("factual") is True
        if src is None:
            if needs_src:
                errors.append(f"{where}: factual post requires source_url")
        elif not isinstance(src, str) or not URL_RE.match(src):
            errors.append(f"{where}: source_url must be an https URL or null")

        if e.get("template") is not None and e["template"] not in {"steps", "cards", "spotlight"}:
            errors.append(f"{where}: unknown template {e['template']!r}")

        text = " ".join(str(e.get(k, "")) for k in ("headline", "caption", "call_to_action")) + " " + " ".join(map(str, pts if isinstance(pts, list) else []))
        hits = [w for w in BANNED if re.search(rf"\b{re.escape(w)}\b", text.lower())]
        if hits:
            errors.append(f"{where}: banned/exaggerated wording {hits}")
    return errors


def load_library(path: Path) -> list[Post]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContentError(f"cannot read content library {path}: {exc}") from exc
    entries = data.get("posts") if isinstance(data, dict) else None
    errors = validate_entries(entries)
    if errors:
        raise ContentError("content library invalid:\n  - " + "\n  - ".join(errors))
    return [Post(id=e["id"], category=e["category"], headline=e["headline"].strip(),
                 points=tuple(p.strip() for p in e["points"]), caption=e["caption"].strip(),
                 call_to_action=e["call_to_action"].strip(), hashtags=tuple(e["hashtags"]),
                 alt_text=e["alt_text"].strip(), source_url=e["source_url"], template=e.get("template"))
            for e in entries]


def select_next(posts: list[Post], history: list[dict], avoid_recent: int = 3) -> Post:
    """Deterministic: first unused post (library order) whose category wasn't used in the last
    `avoid_recent` publications; falls back to the first unused post."""
    used = {h["post_id"] for h in history}
    unused = [p for p in posts if p.id not in used]
    if not unused:
        raise LibraryExhausted("every post in content/posts.json has been published. Add new entries.")
    recent = [h.get("category") for h in history[-avoid_recent:]] if avoid_recent else []
    for p in unused:
        if p.category not in recent:
            return p
    return unused[0]


def get_post(posts: list[Post], post_id: str) -> Post:
    for p in posts:
        if p.id == post_id:
            return p
    raise ContentError(f"no post with id {post_id}")


def build_caption(post: Post, max_hashtags: int = 5) -> str:
    parts = [post.caption, post.call_to_action]
    if post.source_url:
        parts.append(f"Source: {post.source_url}")
    if max_hashtags > 0:
        parts.append(" ".join(post.hashtags[:max_hashtags]))
    caption = "\n\n".join(parts)
    if len(caption) > LIMITS["full_caption"]:
        raise ContentError(f"{post.id}: final caption {len(caption)} chars exceeds Instagram's 2200 limit")
    return caption
