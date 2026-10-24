"""Vegan Leaf command-line interface.

  python -m src.generate_post --validate              validate content/posts.json
  python -m src.generate_post --preview [--id vl-007] [--all]
                                                      render graphics to out/preview (no state change)
  python -m src.generate_post --dry-run               simulate today's run: select, render, caption (no network)
  python -m src.generate_post --build                 CI step 1: select + render + build site/ for GitHub Pages
  python -m src.generate_post --publish               CI step 2: verify hosted image, publish, record state
                                                      (refuses unless PUBLISH_ENABLED=true + credentials)
  python -m src.generate_post --auto                  CI: --publish when fully configured, otherwise --dry-run
  python -m src.generate_post --check                 verify credentials + quota (no posting)
  python -m src.generate_post --history               show publication history

Exit codes: 0 ok/skipped, 1 runtime error, 2 config/content error, 3 state/lock error, 4 credentials error.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from . import config
from .content_manager import (ContentError, LibraryExhausted, build_caption, get_post, load_library,
                              select_next, CATEGORIES)
from .create_graphic import render_post
from .hosting import HostingError, build_site, sha256, verify_public_image
from .publish_instagram import CredentialsError, InstagramClient, InstagramError
from .storage import LockError, StateError, StateStore

log = logging.getLogger("vegan_leaf")

EXIT_OK, EXIT_RUNTIME, EXIT_CONFIG, EXIT_STATE, EXIT_CREDS = 0, 1, 2, 3, 4
RUN_DIR = config.ROOT / ".run"
PLAN_FILE = RUN_DIR / "plan.json"


def today_berlin(override: str | None = None) -> str:
    if override:
        date.fromisoformat(override)
        return override
    return datetime.now(config.TIMEZONE).date().isoformat()


def gh_output(**kv) -> None:
    """Expose values to later workflow steps (no-op locally)."""
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            for k, v in kv.items():
                fh.write(f"{k}={v}\n")


def gh_warning(msg: str) -> None:
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::warning::{msg}")
    else:
        log.warning(msg)


# ---------------------------------------------------------------- commands
def cmd_validate(args) -> int:
    posts = load_library(args.content)
    counts = {c: sum(p.category == c for p in posts) for c in CATEGORIES}
    print(f"OK: {len(posts)} posts valid")
    for c, n in counts.items():
        print(f"  {c:16} {n}")
    return EXIT_OK


def cmd_preview(args) -> int:
    posts = load_library(args.content)
    if args.all:
        chosen = posts
    elif args.id:
        chosen = [get_post(posts, args.id)]
    else:
        store = StateStore(args.state)
        chosen = [select_next(posts, store.history())]
    out = Path(args.out) / "preview"
    for p in chosen:
        r = render_post(p, out)
        print(f"{p.id} [{r.template}] -> {r.png_path}")
        for w in r.warnings:
            gh_warning(w)
    return EXIT_OK


def _now() -> datetime:
    return datetime.now(timezone.utc)


def slot_blocked(store: StateStore, day: str, settings) -> str | None:
    """Why no post is allowed right now (None = allowed).
    Enforces POSTS_PER_DAY and MIN_HOURS_BETWEEN_POSTS (within the same Berlin day), so a
    backup run shortly after a successful run never becomes an extra post."""
    today = store.records_on(day)
    if len(today) >= settings.posts_per_day:
        ids = ", ".join(r["post_id"] for r in today)
        return f"daily limit reached for {day} ({len(today)}/{settings.posts_per_day}: {ids})"
    if today and settings.min_hours_between_posts > 0:
        last = max(datetime.fromisoformat(r["published_at"]) for r in today)
        hours = (_now() - last).total_seconds() / 3600
        if hours < settings.min_hours_between_posts:
            return (f"last post was {hours:.1f}h ago; MIN_HOURS_BETWEEN_POSTS is "
                    f"{settings.min_hours_between_posts:g}h")
    return None


def _plan_today(args, settings) -> tuple[dict | None, object]:
    """Select + render today's post. Returns (plan, post) or (None, None) when already published."""
    store = StateStore(args.state)
    day = today_berlin(args.date)
    blocked = slot_blocked(store, day, settings)
    if blocked:
        print(f"Nothing to do: {blocked}.")
        return None, None
    posts = load_library(args.content)
    history = store.history()
    post = select_next(posts, history)
    remaining = len(posts) - len(history) - 1
    if remaining < 14:
        gh_warning(f"only {remaining} unused posts left in content/posts.json; add more soon")
    r = render_post(post, Path(args.out) / "today", stem=post.id)
    for w in r.warnings:
        gh_warning(w)
    caption = build_caption(post, settings.max_caption_hashtags)
    plan = {"date": day, "post_id": post.id, "category": post.category, "template": r.template,
            "png": str(r.png_path), "jpg": str(r.jpg_path), "jpg_sha256": sha256(r.jpg_path),
            "caption": caption, "alt_text": post.alt_text}
    return plan, post


def cmd_dry_run(args) -> int:
    settings = config.load_settings()
    plan, post = _plan_today(args, settings)
    if plan is None:
        return EXIT_OK
    print(f"DRY RUN for {plan['date']}: would publish {plan['post_id']} ({plan['category']}, "
          f"template {plan['template']})")
    print(f"Image: {plan['png']}  (JPEG for Instagram: {plan['jpg']})")
    print("----- caption -----")
    print(plan["caption"])
    print("----- alt text -----")
    print(plan["alt_text"])
    print("No network calls were made and state was not modified.")
    gh_output(skip="false", post_id=plan["post_id"], dry_run="true")
    return EXIT_OK


def cmd_build(args) -> int:
    settings = config.load_settings()
    plan, post = _plan_today(args, settings)
    if plan is None:
        gh_output(skip="true")
        return EXIT_OK
    rel = build_site(Path(args.site_dir), Path(plan["jpg"]), plan["post_id"])
    plan["site_path"] = rel
    RUN_DIR.mkdir(exist_ok=True)
    PLAN_FILE.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Built site for {plan['post_id']} -> {args.site_dir}/{rel}")
    gh_output(skip="false", post_id=plan["post_id"])
    return EXIT_OK


def _load_plan(day: str) -> dict:
    if not PLAN_FILE.exists():
        raise ContentError("no build plan found: run --build (the workflow does this) before --publish")
    plan = json.loads(PLAN_FILE.read_text(encoding="utf-8"))
    needed = {"date", "post_id", "category", "jpg", "jpg_sha256", "caption", "alt_text", "site_path"}
    if not needed <= set(plan):
        raise ContentError(f"build plan incomplete: missing {sorted(needed - set(plan))}")
    if plan["date"] != day:
        raise ContentError(f"build plan is for {plan['date']}, not {day}; rebuild")
    if sha256(Path(plan["jpg"])) != plan["jpg_sha256"]:
        raise ContentError("rendered image changed since build; rebuild")
    return plan


def cmd_publish(args, client: InstagramClient | None = None, verify=verify_public_image) -> int:
    settings = config.load_settings()
    settings.require_live()  # raises ConfigError -> refuses
    store = StateStore(args.state)
    with store.lock():
        day = today_berlin(args.date)
        blocked = slot_blocked(store, day, settings)
        if blocked:
            print(f"Skipping: {blocked}.")
            return EXIT_OK
        plan = _load_plan(day)
        if plan["post_id"] in store.published_ids():
            raise StateError(f"{plan['post_id']} is already recorded as published; refusing duplicate")
        url = f"{settings.pages_base_url}/{plan['site_path']}"
        print(f"Verifying hosted image {url}")
        verify(url, plan["jpg_sha256"])
        client = client or InstagramClient(user_id=settings.ig_user_id, access_token=settings.ig_access_token,
                                           host=settings.graph_host, api_version=settings.api_version)
        acct = client.verify_credentials()
        print(f"Credentials OK for @{acct.get('username', '?')}")
        result = client.publish_image(url, plan["caption"], plan["alt_text"])
        rec = store.record_published(post_id=plan["post_id"], date=day, media_id=result.media_id,
                                     category=plan["category"], image_url=url,
                                     container_id=result.container_id, recovered=result.recovered)
    verb = "Recovered existing" if result.recovered else "Published"
    print(f"{verb} {rec['post_id']} as Instagram media {rec['media_id']}")
    gh_output(published="true", media_id=rec["media_id"])
    return EXIT_OK


def cmd_auto(args) -> int:
    settings = config.load_settings()
    try:
        settings.require_live()
    except config.ConfigError as exc:
        gh_warning(f"{exc}. Running dry-run instead; nothing will be posted.")
        return cmd_dry_run(args)
    return cmd_publish(args)


def cmd_check(args, client: InstagramClient | None = None) -> int:
    """Verify credentials and quota without posting anything."""
    settings = config.load_settings()
    if not settings.has_credentials:
        raise config.ConfigError("IG_USER_ID and IG_ACCESS_TOKEN must be set for --check")
    client = client or InstagramClient(user_id=settings.ig_user_id, access_token=settings.ig_access_token,
                                       host=settings.graph_host, api_version=settings.api_version)
    acct = client.verify_credentials()
    print(f"Credentials OK: account @{acct.get('username', '?')} ({settings.graph_host})")
    try:
        quota = client.publishing_limit()
        print(f"Publishing quota used in the last 24h: {quota}")
    except InstagramError as exc:
        gh_warning(f"could not read publishing limit (needs instagram_content_publish): {exc}")
    print(f"Live publishing enabled: {settings.publish_enabled}")
    return EXIT_OK


def cmd_history(args) -> int:
    hist = StateStore(args.state).history()
    if not hist:
        print("No posts published yet.")
        return EXIT_OK
    for h in hist:
        flag = " (recovered)" if h.get("recovered") else ""
        print(f"{h['date']}  {h['post_id']}  {h.get('category', ''):16} media={h['media_id']}{flag}")
    print(f"{len(hist)} published")
    return EXIT_OK


# ---------------------------------------------------------------- entry point
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m src.generate_post", description="Vegan Leaf Instagram publisher")
    mode = p.add_mutually_exclusive_group(required=True)
    for flag in ("--validate", "--preview", "--dry-run", "--build", "--publish", "--auto", "--check", "--history"):
        mode.add_argument(flag, action="store_true")
    p.add_argument("--id", help="post id for --preview")
    p.add_argument("--all", action="store_true", help="--preview every post")
    p.add_argument("--date", help="override today's date (YYYY-MM-DD, Europe/Berlin), for testing")
    p.add_argument("--content", default=str(config.CONTENT_FILE))
    p.add_argument("--state", default=str(config.STATE_FILE))
    p.add_argument("--out", default=str(config.ROOT / "out"))
    p.add_argument("--site-dir", default=str(config.ROOT / "site"))
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")
    commands = {"validate": cmd_validate, "preview": cmd_preview, "dry_run": cmd_dry_run, "build": cmd_build,
                "publish": cmd_publish, "auto": cmd_auto, "check": cmd_check, "history": cmd_history}
    name = next(k for k in commands if getattr(args, k))
    try:
        return commands[name](args)
    except CredentialsError as exc:
        print(f"ERROR (credentials): {exc}", file=sys.stderr)
        return EXIT_CREDS
    except (config.ConfigError, ContentError, LibraryExhausted, ValueError) as exc:
        print(f"ERROR (config/content): {exc}", file=sys.stderr)
        return EXIT_CONFIG
    except (StateError, LockError) as exc:
        print(f"ERROR (state): {exc}", file=sys.stderr)
        return EXIT_STATE
    except (InstagramError, HostingError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_RUNTIME


if __name__ == "__main__":
    sys.exit(main())
