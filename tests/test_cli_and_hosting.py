import hashlib
import json

import pytest
import responses

from src import generate_post as gp
from src.config import ConfigError, load_settings
from src.hosting import HostingError, build_site, verify_public_image
from src.publish_instagram import CredentialsError, PublishResult


class FakeClient:
    def __init__(self, media_id="1790000000099", fail=None, recovered=False):
        self.media_id, self.fail, self.recovered = media_id, fail, recovered
        self.published = []

    def verify_credentials(self):
        if self.fail:
            raise self.fail
        return {"id": "17841400000000000", "username": "veganleaf"}

    def publish_image(self, url, caption, alt):
        self.published.append((url, caption, alt))
        return PublishResult(self.media_id, None if self.recovered else "1790000000001", self.recovered)


def no_verify(url, sha):
    assert url.startswith("https://") and len(sha) == 64


# ------------------------------------------------------------- dry run / refusal
@responses.activate  # any real HTTP call would raise ConnectionError
def test_dry_run_makes_no_network_calls_and_keeps_state(isolated, capsys):
    before = isolated.state.read_text()
    assert gp.cmd_dry_run(isolated.args(dry_run=True)) == 0
    assert "DRY RUN" in capsys.readouterr().out
    assert isolated.state.read_text() == before
    assert len(responses.calls) == 0
    assert (isolated.tmp / "out" / "today" / "vl-001.png").exists()


def test_publish_refuses_without_credentials(isolated):
    with pytest.raises(ConfigError, match="IG_ACCESS_TOKEN is missing"):
        gp.cmd_publish(isolated.args(publish=True), client=FakeClient(), verify=no_verify)


def test_publish_refuses_when_not_enabled(isolated, live_env, monkeypatch):
    monkeypatch.setenv("PUBLISH_ENABLED", "false")
    with pytest.raises(ConfigError, match="PUBLISH_ENABLED"):
        gp.cmd_publish(isolated.args(publish=True), client=FakeClient(), verify=no_verify)


def test_main_returns_config_exit_code_and_hides_token(isolated, monkeypatch, capsys):
    monkeypatch.setenv("IG_ACCESS_TOKEN", "short")
    monkeypatch.setenv("PUBLISH_ENABLED", "true")
    code = gp.main(["--publish", "--state", str(isolated.state), "--content", str(isolated.library)])
    err = capsys.readouterr().err
    assert code == gp.EXIT_CONFIG and "short" not in err.replace("IG_ACCESS_TOKEN", "")


def test_auto_falls_back_to_dry_run(isolated, capsys):
    assert gp.cmd_auto(isolated.args(auto=True)) == 0
    assert "DRY RUN" in capsys.readouterr().out


def test_settings_repr_never_contains_token(live_env):
    s = load_settings(use_dotenv=False)
    assert live_env not in repr(s) and live_env not in str(s)


# ------------------------------------------------------------- build + publish
def test_build_then_publish_records_state(isolated, live_env):
    a = isolated.args()
    assert gp.cmd_build(a) == 0
    plan = json.loads(gp.PLAN_FILE.read_text())
    assert (isolated.tmp / "site" / plan["site_path"]).exists()
    fake = FakeClient()
    assert gp.cmd_publish(a, client=fake, verify=no_verify) == 0
    url, caption, alt = fake.published[0]
    assert url == "https://example.github.io/vegan-leaf-automation/images/vl-001.jpg"
    state = json.loads(isolated.state.read_text())
    assert state["published"][0]["post_id"] == "vl-001"
    assert state["published"][0]["media_id"] == "1790000000099"
    assert live_env not in isolated.state.read_text()


def test_second_run_same_day_is_a_noop(isolated, live_env):
    a = isolated.args()
    gp.cmd_build(a)
    gp.cmd_publish(a, client=FakeClient(), verify=no_verify)
    fake = FakeClient()
    assert gp.cmd_build(a) == 0          # skip, nothing rendered
    assert gp.cmd_publish(a, client=fake, verify=no_verify) == 0
    assert fake.published == []
    assert len(json.loads(isolated.state.read_text())["published"]) == 1


def test_next_day_selects_next_post(isolated, live_env):
    gp.cmd_build(isolated.args())
    gp.cmd_publish(isolated.args(), client=FakeClient(), verify=no_verify)
    gp.cmd_build(isolated.args(date="2026-10-22"))
    gp.cmd_publish(isolated.args(date="2026-10-22"), client=FakeClient(media_id="1790000000100"), verify=no_verify)
    ids = [r["post_id"] for r in json.loads(isolated.state.read_text())["published"]]
    assert ids == ["vl-001", "vl-002"]


def test_failed_publish_does_not_mark_published(isolated, live_env):
    gp.cmd_build(isolated.args())
    with pytest.raises(CredentialsError):
        gp.cmd_publish(isolated.args(), client=FakeClient(fail=CredentialsError("expired")), verify=no_verify)
    assert json.loads(isolated.state.read_text())["published"] == []


def test_hosting_failure_stops_before_instagram(isolated, live_env):
    gp.cmd_build(isolated.args())
    fake = FakeClient()

    def bad_verify(url, sha):
        raise HostingError("404")
    with pytest.raises(HostingError):
        gp.cmd_publish(isolated.args(), client=fake, verify=bad_verify)
    assert fake.published == []


def test_retry_after_lost_state_commit_recovers(isolated, live_env):
    """Run 1 posted but its state commit was lost; run 2 must record, not re-post."""
    gp.cmd_build(isolated.args())
    assert gp.cmd_publish(isolated.args(), client=FakeClient(recovered=True), verify=no_verify) == 0
    rec = json.loads(isolated.state.read_text())["published"][0]
    assert rec["recovered"] is True


def test_publish_without_build_plan_refuses(isolated, live_env):
    with pytest.raises(Exception, match="no build plan"):
        gp.cmd_publish(isolated.args(), client=FakeClient(), verify=no_verify)


def test_stale_plan_from_other_day_refuses(isolated, live_env):
    gp.cmd_build(isolated.args())
    with pytest.raises(Exception, match="rebuild"):
        gp.cmd_publish(isolated.args(date="2026-10-22"), client=FakeClient(), verify=no_verify)


def test_corrupt_state_blocks_publishing(isolated, live_env):
    gp.cmd_build(isolated.args())
    isolated.state.write_text("{broken")
    fake = FakeClient()
    code = gp.main(["--publish", "--date", "2026-10-21", "--state", str(isolated.state),
                    "--content", str(isolated.library)])
    assert code == gp.EXIT_STATE and fake.published == []


def test_history_command(isolated, live_env, capsys):
    gp.cmd_build(isolated.args())
    gp.cmd_publish(isolated.args(), client=FakeClient(), verify=no_verify)
    gp.cmd_history(isolated.args(history=True))
    assert "vl-001" in capsys.readouterr().out


def test_build_writes_github_outputs(isolated, monkeypatch):
    out = isolated.tmp / "gh_out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    gp.cmd_build(isolated.args())
    assert "skip=false" in out.read_text() and "post_id=vl-001" in out.read_text()


# ------------------------------------------------------------- hosting
def test_build_site_contains_only_todays_image(tmp_path):
    jpg = tmp_path / "a.jpg"
    jpg.write_bytes(b"\xff\xd8data")
    site = tmp_path / "site"
    (site / "old").mkdir(parents=True)
    rel = build_site(site, jpg, "vl-001")
    assert rel == "images/vl-001.jpg" and (site / rel).read_bytes() == b"\xff\xd8data"
    assert not (site / "old").exists()


URL = "https://example.github.io/repo/images/vl-001.jpg"
DATA = b"\xff\xd8\xff\xe0jpegbytes"
SHA = hashlib.sha256(DATA).hexdigest()


@responses.activate
def test_verify_public_image_ok():
    responses.get(URL, body=DATA, content_type="image/jpeg")
    verify_public_image(URL, SHA, sleep=lambda s: None)


@responses.activate
def test_verify_waits_for_new_deployment():
    responses.get(URL, status=404)
    responses.get(URL, body=b"old image", content_type="image/jpeg")
    responses.get(URL, body=DATA, content_type="image/jpeg")
    verify_public_image(URL, SHA, attempts=5, sleep=lambda s: None)
    assert len(responses.calls) == 3


@responses.activate
def test_verify_rejects_wrong_content_type():
    responses.get(URL, body=DATA, content_type="text/html")
    with pytest.raises(HostingError, match="content-type"):
        verify_public_image(URL, SHA, attempts=2, sleep=lambda s: None)


def test_verify_rejects_http_url():
    with pytest.raises(HostingError):
        verify_public_image("http://example.com/x.jpg", SHA)


def test_check_requires_credentials(isolated):
    with pytest.raises(ConfigError):
        gp.cmd_check(isolated.args())


def test_check_reports_account(isolated, live_env, capsys):
    class C(FakeClient):
        def publishing_limit(self):
            return 0
    assert gp.cmd_check(isolated.args(), client=C()) == 0
    out = capsys.readouterr().out
    assert "@veganleaf" in out and live_env not in out
