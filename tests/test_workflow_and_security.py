import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WF = ROOT / ".github" / "workflows" / "daily-post.yml"


def wf():
    data = yaml.safe_load(WF.read_text(encoding="utf-8"))
    data["on"] = data.pop(True, data.get("on"))  # PyYAML parses the key `on` as boolean True
    return data


def test_schedule_is_18_00_berlin_with_backup():
    sched = wf()["on"]["schedule"]
    assert {"cron": "0 18 * * *", "timezone": "Europe/Berlin"} in sched
    assert all(s.get("timezone") == "Europe/Berlin" for s in sched)


def test_manual_dispatch_defaults_to_dry_run():
    inputs = wf()["on"]["workflow_dispatch"]["inputs"]["mode"]
    assert inputs["default"] == "dry-run" and set(inputs["options"]) == {"dry-run", "check", "publish"}


def test_concurrency_prevents_overlap():
    c = wf()["concurrency"]
    assert c["group"] and c["cancel-in-progress"] is False


def test_minimal_permissions_and_timeouts():
    data = wf()
    assert data["permissions"] == {}
    for name, job in data["jobs"].items():
        assert job["timeout-minutes"] <= 30, name
        assert "permissions" in job, name
    assert data["jobs"]["test"]["permissions"] == {"contents": "read"}
    assert set(data["jobs"]["publish"]["permissions"]) == {"contents", "pages", "id-token"}


def test_tests_gate_publishing():
    assert wf()["jobs"]["publish"]["needs"] == "test"
    steps = " ".join(str(s.get("run", "")) for s in wf()["jobs"]["test"]["steps"])
    assert "pytest" in steps and "--validate" in steps


def test_secrets_only_from_github_secrets():
    text = WF.read_text()
    assert "${{ secrets.IG_ACCESS_TOKEN }}" in text and "${{ secrets.IG_USER_ID }}" in text
    assert not re.search(r"IG_ACCESS_TOKEN:\s*['\"]?[A-Za-z0-9]{20,}", text)


def test_actions_are_pinned_to_commit_shas():
    for job in wf()["jobs"].values():
        for step in job["steps"]:
            if "uses" in step:
                assert re.search(r"@[0-9a-f]{40}$", step["uses"]), step["uses"]


def test_no_untrusted_template_expansion_in_run_scripts():
    for job in wf()["jobs"].values():
        for step in job["steps"]:
            run = step.get("run", "")
            assert "${{ github.event" not in run and "${{ inputs" not in run, step.get("name")


def test_publish_checkout_does_not_persist_credentials():
    checkout = wf()["jobs"]["publish"]["steps"][0]
    assert checkout["with"]["persist-credentials"] is False


def test_gitignore_covers_secrets_and_outputs():
    gi = (ROOT / ".gitignore").read_text()
    for pattern in (".env", "__pycache__/", "out/", "site/", ".run/", "*.log"):
        assert pattern in gi


def test_env_example_contains_placeholders_only():
    for line in (ROOT / ".env.example").read_text().splitlines():
        if line.startswith(("IG_USER_ID=", "IG_ACCESS_TOKEN=")):
            assert line.split("=", 1)[1].strip() == ""


TOKEN_PATTERNS = [r"EAA[A-Za-z0-9]{30,}", r"IGQV[A-Za-z0-9_-]{30,}", r"ghp_[A-Za-z0-9]{30,}",
                  r"sk-ant-[A-Za-z0-9-]{20,}"]


def test_no_token_like_strings_in_tracked_files():
    files = [p for p in ROOT.rglob("*") if p.is_file() and p.suffix in {".py", ".json", ".yml", ".md", ".txt",
                                                                        ".example", ""}
             and not any(part in {".git", "out", "site", ".run", "__pycache__", "tests"} for part in p.parts)]
    for f in files:
        text = f.read_text(encoding="utf-8", errors="ignore")
        for pat in TOKEN_PATTERNS:
            assert not re.search(pat, text), f"possible secret in {f}"


def test_cli_help_runs():
    out = subprocess.run(["python", "-m", "src.generate_post", "--help"], cwd=ROOT, capture_output=True, text=True)
    assert out.returncode == 0 and "--publish" in out.stdout
