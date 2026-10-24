"""Pillow graphic generator: 1080x1350 branded educational cards (no photos, no AI imagery).

Three reusable templates:
  steps      numbered list (recipes, guides, plans)
  cards      each point on its own rounded card (swaps, shopping, ingredients)
  spotlight  forest-green hero headline over a list (facts, myths, questions)

Outputs a PNG master and a JPEG copy. The Instagram Graph API only accepts JPEG for
image posts, so the JPEG is the one that gets published.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .config import FONTS_DIR

log = logging.getLogger(__name__)

W, H = 1080, 1350
MARGIN = 88
SS = 2  # supersampling for smooth edges

PALETTE = {
    "forest": (0x24, 0x4B, 0x37),   # #244B37
    "sage": (0xA8, 0xC3, 0xA0),     # #A8C3A0
    "cream": (0xF6, 0xF1, 0xE7),    # #F6F1E7
    "orange": (0xE7, 0xA8, 0x5B),   # #E7A85B
    "ink": (0x1C, 0x2A, 0x22),      # dark text derived from forest
    "card": (0xFC, 0xF9, 0xF3),     # lifted cream for cards
}

CATEGORY_TEMPLATE = {
    "quick_recipes": "steps", "meal_planning": "steps", "beginner_guides": "steps",
    "substitutions": "cards", "grocery_tips": "cards", "ingredients": "cards",
    "food_facts": "spotlight", "myths": "spotlight", "sustainability": "spotlight", "questions": "spotlight",
}

FONT_FILES = {
    "display": ["Lora-Variable.ttf"],
    "body": ["Inter-Regular.otf"],
    "body_bold": ["Inter-SemiBold.otf"],
    "label": ["Inter-Bold.otf"],
}
SYSTEM_FALLBACKS = {
    "display": ["DejaVuSerif-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
                "C:/Windows/Fonts/georgiab.ttf"],
    "body": ["DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "C:/Windows/Fonts/arial.ttf"],
    "body_bold": ["DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                  "C:/Windows/Fonts/arialbd.ttf"],
    "label": ["DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "C:/Windows/Fonts/arialbd.ttf"],
}


class Fonts:
    """Resolves brand fonts, falling back to system fonts and finally Pillow's built-in font."""

    def __init__(self, fonts_dir: Path = FONTS_DIR):
        self.fonts_dir = Path(fonts_dir)
        self.warnings: list[str] = []
        self._cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}

    def get(self, role: str, size: int):
        key = (role, size)
        if key in self._cache:
            return self._cache[key]
        px = size * SS
        candidates = [str(self.fonts_dir / f) for f in FONT_FILES[role]] + SYSTEM_FALLBACKS[role]
        font = None
        for i, cand in enumerate(candidates):
            # Pillow silently searches system font dirs by basename, so require bundled files to exist.
            if i < len(FONT_FILES[role]) and not Path(cand).is_file():
                continue
            try:
                font = ImageFont.truetype(cand, px)
                if role == "display" and cand.endswith("Lora-Variable.ttf"):
                    try:
                        font.set_variation_by_name("Bold")
                    except Exception:  # noqa: BLE001 - non-variable build of the font
                        pass
                if i >= len(FONT_FILES[role]):
                    self._warn(f"brand font for '{role}' missing; using fallback {Path(cand).name}")
                break
            except OSError:
                continue
        if font is None:
            self._warn(f"no TrueType font for '{role}'; using Pillow default font")
            font = ImageFont.load_default(size=px)
        self._cache[key] = font
        return font

    def _warn(self, msg: str) -> None:
        if msg not in self.warnings:
            self.warnings.append(msg)
            log.warning(msg)


@dataclass
class RenderResult:
    png_path: Path
    jpg_path: Path
    template: str
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- text helpers
def wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    """Greedy word wrap; words longer than the line are hard-split."""
    lines: list[str] = []
    cur = ""
    for word in text.split():
        while draw.textlength(word, font=font) > max_w and len(word) > 1:
            cut = len(word)
            while cut > 1 and draw.textlength(word[:cut] + "-", font=font) > max_w:
                cut -= 1
            if cur:
                lines.append(cur)
                cur = ""
            lines.append(word[:cut] + "-")
            word = word[cut:]
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


@dataclass
class Fitted:
    font: object
    lines: list[str]
    line_h: int  # supersampled px
    size: int
    truncated: bool = False

    @property
    def height(self) -> int:
        return self.line_h * len(self.lines)


def fit_text(draw, fonts: Fonts, role: str, text: str, max_w: int, max_h: int,
             start: int, minimum: int, leading: float = 1.2, max_lines: int | None = None) -> Fitted:
    """Largest size in [minimum, start] that fits max_w x max_h (unscaled px).
    If even `minimum` overflows, lines are truncated with an ellipsis (never drawn outside the box)."""
    for size in range(start, minimum - 1, -2):
        f = fonts.get(role, size)
        lines = wrap(draw, text, f, max_w * SS)
        lh = int(size * leading * SS)
        if lh * len(lines) <= max_h * SS and (max_lines is None or len(lines) <= max_lines):
            return Fitted(f, lines, lh, size)
    f = fonts.get(role, minimum)
    lh = int(minimum * leading * SS)
    lines = wrap(draw, text, f, max_w * SS)
    allowed = max(1, min(max_h * SS // lh, max_lines or 10**6))
    if len(lines) > allowed:
        lines = lines[:allowed]
        last = lines[-1]
        while last and draw.textlength(last + "…", font=f) > max_w * SS:
            last = last[:-1].rstrip()
        lines[-1] = last + "…"
        return Fitted(f, lines, lh, minimum, truncated=True)
    return Fitted(f, lines, lh, minimum)


def fit_points(draw, fonts: Fonts, points: list[str], max_w: int, max_h: int,
               start: int, minimum: int, gap_ratio: float, extra_per_item: int = 0):
    """Pick one shared size for all points so the list fits the box."""
    for size in range(start, minimum - 1, -2):
        f = fonts.get("body", size)
        lh = int(size * 1.3 * SS)
        gap = int(size * gap_ratio * SS)
        wrapped = [wrap(draw, p, f, max_w * SS) for p in points]
        total = sum(len(w) * lh + extra_per_item * SS for w in wrapped) + gap * (len(points) - 1)
        if total <= max_h * SS:
            return f, lh, gap, wrapped, size, False, total
    # still too tall: cap lines per point at 2, mark truncated
    f = fonts.get("body", minimum)
    lh = int(minimum * 1.3 * SS)
    gap = int(minimum * gap_ratio * SS)
    wrapped = []
    for p in points:
        w = wrap(draw, p, f, max_w * SS)
        if len(w) > 2:
            w = w[:2]
            w[-1] = w[-1].rstrip(".,;:") + "…"
        wrapped.append(w)
    total = sum(len(w) * lh + extra_per_item * SS for w in wrapped) + gap * (len(points) - 1)
    return f, lh, gap, wrapped, minimum, True, total


# ---------------------------------------------------------------- brand elements
def _center_offset(avail_px: int, total_px: int, cap: int = 110 * SS) -> int:
    """Push a short list down so it sits in the optical middle of the free space."""
    return max(0, min((avail_px - total_px) // 2, cap))


def _mix(a, b, t):
    return tuple(int(x * (1 - t) + y * t) for x, y in zip(a, b))


def draw_leaf(draw, cx, cy, size, angle, fill, vein):
    pts = []
    n = 48
    for i in range(n + 1):
        t = i / n
        pts.append(((t - 0.5) * size, math.sin(math.pi * t) * size * 0.27))
    for i in range(n, -1, -1):
        t = i / n
        pts.append(((t - 0.5) * size, -math.sin(math.pi * t) * size * 0.27))
    a = math.radians(angle)
    rot = [(cx + x * math.cos(a) - y * math.sin(a), cy + x * math.sin(a) + y * math.cos(a)) for x, y in pts]
    draw.polygon(rot, fill=fill)
    if vein:
        x0, y0 = cx - 0.4 * size * math.cos(a), cy - 0.4 * size * math.sin(a)
        x1, y1 = cx + 0.4 * size * math.cos(a), cy + 0.4 * size * math.sin(a)
        draw.line([(x0, y0), (x1, y1)], fill=vein, width=max(2, int(size * 0.025)))


def draw_pill(draw, fonts, x, y, text, bg, fg) -> int:
    f = fonts.get("label", 24)
    tw = draw.textlength(text, font=f)
    h = 52 * SS
    draw.rounded_rectangle([x, y, x + tw + 44 * SS, y + h], radius=h // 2, fill=bg)
    draw.text((x + 22 * SS, y + h // 2), text, font=f, fill=fg, anchor="lm")
    return y + h


def draw_wordmark(draw, fonts, x, y_center, color, leaf_fill, handle_color=None, handle=None):
    draw_leaf(draw, x + 24 * SS, y_center, 56 * SS, -40, leaf_fill, color if leaf_fill != color else None)
    f = fonts.get("display", 36)
    draw.text((x + 66 * SS, y_center), "Vegan Leaf", font=f, fill=color, anchor="lm")
    if handle:
        hf = fonts.get("body", 26)
        draw.text((W * SS - MARGIN * SS, y_center), handle, font=hf, fill=handle_color or color, anchor="rm")


# ---------------------------------------------------------------- templates
def _draw_lines(draw, fitted: Fitted, x, y, fill) -> int:
    for line in fitted.lines:
        draw.text((x, y), line, font=fitted.font, fill=fill)
        y += fitted.line_h
    return y


def _template_steps(img, d, fonts, post, handle):
    c = PALETTE
    S = SS
    draw_leaf(d, (W - 30) * S, 90 * S, 330 * S, -35, _mix(c["cream"], c["sage"], 0.45), c["cream"])
    y = draw_pill(d, fonts, MARGIN * S, MARGIN * S, post.label, c["sage"], c["forest"]) + 40 * S
    body_w = W - 2 * MARGIN
    head = fit_text(d, fonts, "display", post.headline, body_w - 40, 270, 84, 50, 1.12, max_lines=3)
    y = _draw_lines(d, head, MARGIN * S, y, c["forest"]) + 30 * S
    d.rounded_rectangle([MARGIN * S, y, (MARGIN + 110) * S, y + 8 * S], radius=4 * S, fill=c["orange"])
    y += 50 * S
    footer_top = H - 120
    avail = footer_top - 50 - y // S
    f, lh, gap, wrapped, size, trunc, total = fit_points(d, fonts, list(post.points), body_w - 92,
                                                         avail, 50, 28, 0.8)
    y += _center_offset(avail * S, total, 70 * S)
    nf = fonts.get("label", int(size * 0.7))
    r = int(size * 0.66 * S)
    for i, lines in enumerate(wrapped, 1):
        cy = y + lh // 2
        d.ellipse([MARGIN * S, cy - r, MARGIN * S + 2 * r, cy + r], fill=c["forest"])
        d.text((MARGIN * S + r, cy), str(i), font=nf, fill=c["cream"], anchor="mm")
        for line in lines:
            d.text(((MARGIN + 92) * S, y + lh // 2), line, font=f, fill=c["ink"], anchor="lm")
            y += lh
        y += gap
    d.rectangle([0, footer_top * S, W * S, H * S], fill=c["forest"])
    draw_wordmark(d, fonts, MARGIN * S, (footer_top + 60) * S, c["cream"], c["sage"], c["sage"], handle)
    return head.truncated or trunc


def _template_cards(img, d, fonts, post, handle):
    c = PALETTE
    S = SS
    draw_leaf(d, 40 * S, (H - 220) * S, 300 * S, 50, _mix(c["cream"], c["sage"], 0.4), c["cream"])
    y = draw_pill(d, fonts, MARGIN * S, MARGIN * S, post.label, c["forest"], c["cream"]) + 40 * S
    body_w = W - 2 * MARGIN
    head = fit_text(d, fonts, "display", post.headline, body_w, 260, 80, 48, 1.12, max_lines=3)
    y = _draw_lines(d, head, MARGIN * S, y, c["forest"]) + 44 * S
    footer_top = H - 110
    pad = 30
    avail = footer_top - 40 - y // S
    f, lh, gap, wrapped, size, trunc, total = fit_points(d, fonts, list(post.points), body_w - 2 * pad - 24,
                                                         avail, 46, 26, 0.6, extra_per_item=2 * pad)
    y += _center_offset(avail * S, total, 60 * S)
    for lines in wrapped:
        card_h = len(lines) * lh + 2 * pad * S
        d.rounded_rectangle([MARGIN * S, y, (W - MARGIN) * S, y + card_h], radius=22 * S, fill=c["card"],
                            outline=_mix(c["cream"], c["sage"], 0.6), width=2 * S)
        d.rounded_rectangle([MARGIN * S, y, (MARGIN + 12) * S, y + card_h], radius=6 * S, fill=c["orange"])
        ty = y + pad * S
        for line in lines:
            d.text(((MARGIN + pad + 16) * S, ty + lh // 2), line, font=f, fill=c["ink"], anchor="lm")
            ty += lh
        y += card_h + gap
    d.line([(MARGIN * S, footer_top * S), ((W - MARGIN) * S, footer_top * S)], fill=c["sage"], width=2 * S)
    draw_wordmark(d, fonts, MARGIN * S, (footer_top + 55) * S, c["forest"], c["sage"], c["forest"], handle)
    return head.truncated or trunc


def _template_spotlight(img, d, fonts, post, handle):
    c = PALETTE
    S = SS
    body_w = W - 2 * MARGIN
    # measure headline first to size the hero block
    head = fit_text(d, fonts, "display", post.headline, body_w - 20, 330, 86, 52, 1.1, max_lines=4)
    hero_h = MARGIN + 52 + 44 + head.height // S + 70
    d.rectangle([0, 0, W * S, hero_h * S], fill=c["forest"])
    draw_leaf(d, (W - 60) * S, (hero_h - 40) * S, 300 * S, -30, _mix(c["forest"], c["sage"], 0.25), None)
    y = draw_pill(d, fonts, MARGIN * S, MARGIN * S, post.label, c["orange"], c["forest"]) + 44 * S
    _draw_lines(d, head, MARGIN * S, y, c["cream"])
    y = (hero_h + 60) * S
    footer_top = H - 110
    avail = footer_top - 40 - y // S
    f, lh, gap, wrapped, size, trunc, total = fit_points(d, fonts, list(post.points), body_w - 64,
                                                         avail, 48, 26, 0.85)
    y += _center_offset(avail * S, total)
    for lines in wrapped:
        cy = y + lh // 2
        r = int(size * 0.32 * S)
        d.ellipse([(MARGIN + 6) * S, cy - r, (MARGIN + 6) * S + 2 * r, cy + r], fill=c["orange"])
        for line in lines:
            d.text(((MARGIN + 64) * S, y + lh // 2), line, font=f, fill=c["ink"], anchor="lm")
            y += lh
        y += gap
    d.line([(MARGIN * S, footer_top * S), ((W - MARGIN) * S, footer_top * S)], fill=c["sage"], width=2 * S)
    draw_wordmark(d, fonts, MARGIN * S, (footer_top + 55) * S, c["forest"], c["sage"], c["forest"], handle)
    return head.truncated or trunc


TEMPLATES = {"steps": _template_steps, "cards": _template_cards, "spotlight": _template_spotlight}


def choose_template(post) -> str:
    return post.template or CATEGORY_TEMPLATE.get(post.category, "steps")


def render_post(post, out_dir: Path, *, stem: str | None = None, handle: str = "@veganleaf",
                fonts: Fonts | None = None, template: str | None = None) -> RenderResult:
    fonts = fonts or Fonts()
    tpl = template or choose_template(post)
    if tpl not in TEMPLATES:
        raise ValueError(f"unknown template {tpl}")
    img = Image.new("RGB", (W * SS, H * SS), PALETTE["cream"])
    d = ImageDraw.Draw(img)
    truncated = TEMPLATES[tpl](img, d, fonts, post, handle)
    img = img.resize((W, H), Image.LANCZOS)

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = stem or post.id
    png = out_dir / f"{stem}.png"
    jpg = out_dir / f"{stem}.jpg"
    img.save(png, "PNG", optimize=True)
    # Baseline JPEG, no EXIF/metadata: Graph API rejects extended JPEG formats (MPO/JPS).
    img.save(jpg, "JPEG", quality=92, optimize=True, progressive=False)
    warnings = list(fonts.warnings)
    if truncated:
        warnings.append(f"{post.id}: text was truncated to fit; shorten it in posts.json")
        log.warning(warnings[-1])
    return RenderResult(png, jpg, tpl, warnings)
