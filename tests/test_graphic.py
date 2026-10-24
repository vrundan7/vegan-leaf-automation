import pytest
from PIL import Image

from src.content_manager import Post
from src.create_graphic import H, TEMPLATES, W, Fonts, render_post


def _post(**over):
    base = dict(id="vl-900", category="quick_recipes", headline="Five-Minute Hummus Wrap",
                points=("Spread hummus on a wrap", "Add crunchy veg", "Roll and slice"),
                caption="c", call_to_action="cta", hashtags=("#vegan",) * 5, alt_text="alt", source_url=None)
    base.update(over)
    return Post(**base)


@pytest.mark.parametrize("template", sorted(TEMPLATES))
def test_each_template_renders_png_and_jpeg_at_instagram_size(tmp_path, template):
    r = render_post(_post(), tmp_path, template=template)
    with Image.open(r.png_path) as png:
        assert png.format == "PNG" and png.size == (W, H) == (1080, 1350)
    with Image.open(r.jpg_path) as jpg:
        assert jpg.format == "JPEG" and jpg.size == (1080, 1350) and jpg.mode == "RGB"
        assert not jpg.info.get("progressive")  # baseline JPEG for the Graph API
    assert r.warnings == []


def test_template_chosen_by_category(tmp_path):
    assert render_post(_post(category="myths"), tmp_path).template == "spotlight"
    assert render_post(_post(category="substitutions"), tmp_path).template == "cards"
    assert render_post(_post(category="meal_planning"), tmp_path).template == "steps"


def test_explicit_template_override(tmp_path):
    assert render_post(_post(template="cards"), tmp_path).template == "cards"


@pytest.mark.parametrize("template", sorted(TEMPLATES))
def test_overflowing_text_is_truncated_not_drawn_off_canvas(tmp_path, template):
    long = " ".join(["supercalifragilistic"] * 40)
    post = _post(headline=long, points=(long, long, long, long, long))
    r = render_post(post, tmp_path, template=template)
    assert any("truncated" in w for w in r.warnings)
    with Image.open(r.png_path) as img:
        assert img.size == (1080, 1350)


def test_unbreakable_word_is_wrapped(tmp_path):
    r = render_post(_post(headline="A" * 120), tmp_path)
    assert r.png_path.exists()


def test_missing_brand_fonts_fall_back_gracefully(tmp_path):
    fonts = Fonts(fonts_dir=tmp_path / "does-not-exist")
    r = render_post(_post(), tmp_path, fonts=fonts)
    assert r.png_path.exists()
    assert any("missing" in w or "default" in w for w in r.warnings)


def test_no_fonts_at_all_uses_pillow_default(tmp_path, monkeypatch):
    import src.create_graphic as cg
    monkeypatch.setattr(cg, "SYSTEM_FALLBACKS", {k: [] for k in cg.SYSTEM_FALLBACKS})
    fonts = Fonts(fonts_dir=tmp_path / "none")
    r = render_post(_post(), tmp_path, fonts=fonts)
    assert any("Pillow default" in w for w in r.warnings)
    with Image.open(r.png_path) as img:
        assert img.size == (1080, 1350)


def test_brand_palette_present(tmp_path):
    r = render_post(_post(category="myths"), tmp_path)
    with Image.open(r.png_path) as img:
        colors = {c for _, c in img.getcolors(maxcolors=1_000_000)}
    assert (0x24, 0x4B, 0x37) in colors   # forest
    assert (0xF6, 0xF1, 0xE7) in colors   # cream
