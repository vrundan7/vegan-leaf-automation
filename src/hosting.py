"""Public image hosting via GitHub Pages.

The workflow deploys the `site/` directory with actions/deploy-pages. This module builds the
site directory and verifies, before Instagram is called, that the URL is live, is
served as image/jpeg, and returns exactly the bytes we rendered (so a stale earlier
deployment can never be published by mistake).
"""
from __future__ import annotations

import hashlib
import shutil
import time
from pathlib import Path
from typing import Callable

import requests


class HostingError(RuntimeError):
    pass


def build_site(site_dir: Path, jpg: Path, post_id: str) -> str:
    """Create a minimal Pages site containing only today's image. Returns the relative path."""
    site_dir = Path(site_dir)
    if site_dir.exists():
        shutil.rmtree(site_dir)
    (site_dir / "images").mkdir(parents=True)
    rel = f"images/{post_id}.jpg"
    shutil.copyfile(jpg, site_dir / rel)
    (site_dir / "index.html").write_text(
        "<!doctype html><meta charset=utf-8><title>Vegan Leaf media</title>"
        "<p>Media host for the Vegan Leaf Instagram account.</p>", encoding="utf-8")
    return rel


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_public_image(url: str, expected_sha256: str, *, session: requests.Session | None = None,
                        attempts: int = 20, delay: float = 15.0,
                        sleep: Callable[[float], None] = time.sleep) -> None:
    if not url.startswith("https://"):
        raise HostingError("image URL must be https")
    s = session or requests.Session()
    last = "no attempt"
    for i in range(attempts):
        try:
            r = s.get(url, timeout=30, headers={"Cache-Control": "no-cache"})
            ctype = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if r.status_code != 200:
                last = f"HTTP {r.status_code}"
            elif ctype != "image/jpeg":
                last = f"content-type {ctype!r} (need image/jpeg)"
            elif hashlib.sha256(r.content).hexdigest() != expected_sha256:
                last = "served bytes differ from the rendered image (old deployment still cached)"
            else:
                return
        except requests.RequestException as exc:
            last = f"network error: {type(exc).__name__}"
        if i < attempts - 1:
            sleep(delay)
    raise HostingError(f"image not publicly available at {url}: {last}")
