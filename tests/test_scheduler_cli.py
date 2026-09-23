import json

import yaml

from conftest import StaticHost, make_post, write_config
from instagram_os import cli
from instagram_os.client import AuthError
from instagram_os.reasoner import RulesReasoner
from instagram_os.scheduler import Runtime, crontab_lines, run_job


def runtime(brand, store, fake):
    return Runtime(brand, client=fake, reasoner=RulesReasoner(), media_host=StaticHost(), store=store)


def test_daily_run_executes_every_stage(brand, store, fake):
    make_post(brand)
    rt = runtime(brand, store, fake)
    status, summary = run_job(rt, "daily-run")
    assert status == "OK"
    for job in ("token-check", "publish", "comments", "opportunities", "analytics"):
        assert f"{job}: OK" in summary
    assert len(fake.media) == 1
    assert {r["job"] for r in store.job_runs()} >= {"daily-run", "publish", "comments", "analytics"}


def test_overlapping_runs_are_skipped(brand, store, fake):
    rt = runtime(brand, store, fake)
    assert store.acquire_lock("job:publish")
    assert run_job(rt, "publish") == ("SKIPPED", "already running")


def test_job_failures_are_recorded_not_raised(brand, store, fake):
    fake.fail["get_account"] = [AuthError("expired", code=190)]
    status, summary = run_job(runtime(brand, store, fake), "token-check")
    assert status == "FAILED" and "AuthError" in summary
    assert store.job_runs(1)[0]["status"] == "FAILED"
    assert store.acquire_lock("job:token-check")  # lock released even on failure


def test_daily_run_stops_on_auth_failure(brand, store, fake):
    fake.fail["get_account"] = [AuthError("expired", code=190)]
    status, summary = run_job(runtime(brand, store, fake), "daily-run")
    assert "stopped: fix authentication first" in summary
    assert "publish:" not in summary


def test_plan_writes_brief_and_records_human_action(brand, store, fake):
    status, summary = run_job(runtime(brand, store, fake), "plan")
    assert status == "OK" and "human action needed" in summary
    assert "Planning brief" in (brand.instagram_dir / "plan-brief.md").read_text()


def test_plan_hook_command(brand, store, fake, tmp_path):
    marker = tmp_path / "ran"
    write_config(brand, scheduler={"plan_command": f"echo {{brand}} > {marker}"})
    status, _ = run_job(runtime(brand, store, fake), "plan")
    assert status == "OK" and marker.read_text().strip() == "test-brand"


def test_token_check_warns_before_expiry(brand, store, fake, monkeypatch):
    from datetime import timedelta
    from instagram_os.store import iso, utcnow
    monkeypatch.setenv("INSTAGRAM_TOKEN_EXPIRES_AT", iso(utcnow() + timedelta(days=3)))
    status, summary = run_job(runtime(brand, store, fake), "token-check")
    assert status == "OK" and "WARNING" in summary


def test_token_refresh_via_sink(brand, store, fake, monkeypatch, tmp_path):
    from datetime import timedelta
    from instagram_os.store import iso, utcnow
    sink = tmp_path / "token"
    write_config(brand, scheduler={"token_sink_command": f"cat > {sink}"})
    monkeypatch.setenv("INSTAGRAM_TOKEN_EXPIRES_AT", iso(utcnow() + timedelta(days=3)))
    status, summary = run_job(runtime(brand, store, fake), "token-check")
    assert "refreshed" in summary and sink.read_text() == "new-token"
    assert "new-token" not in (brand.instagram_dir / "activity.log").read_text()


def test_crontab_lines(brand):
    text = crontab_lines(brand)
    assert "--brand test-brand comments" in text and "*/30 * * * *" in text


def test_cli_end_to_end_dry_run(brand, tmp_path, monkeypatch, capsys):
    """The real CLI path with no credentials: dry-run everything, nothing leaves the machine."""
    registry = tmp_path / "brands.local.json"
    cfg = yaml.safe_load((brand.instagram_dir / "config.yaml").read_text())
    cfg["api"]["mode"] = "dry_run"
    (brand.instagram_dir / "config.yaml").write_text(yaml.safe_dump(cfg))
    for var in ("INSTAGRAM_ACCESS_TOKEN", "INSTAGRAM_ACCOUNT_ID", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    make_post(brand)
    args = ["--brand", "test-brand", "--registry", str(registry)]
    assert cli.main(args + ["daily-run"]) == 0
    assert cli.main(args + ["status"]) == 0
    out = capsys.readouterr().out
    assert "API mode: dry_run" in out and "third_party_comment" in out and "locked" in out
    assert cli.main(args + ["queue"]) == 0
    assert "QUEUED" in capsys.readouterr().out  # dry-run never marks PUBLISHED
    assert cli.main(args + ["log"]) == 0
    assert "Would publish" in capsys.readouterr().out
    assert cli.main(args + ["opportunities", "list"]) == 0
    assert cli.main(args + ["weekly-review"]) == 0


def test_cli_refuses_disabled_brand(brand, tmp_path, capsys):
    cfg = yaml.safe_load((brand.instagram_dir / "config.yaml").read_text())
    cfg["enabled"] = False
    (brand.instagram_dir / "config.yaml").write_text(yaml.safe_dump(cfg))
    assert cli.main(["--brand", "test-brand", "--registry", str(tmp_path / "brands.local.json"), "publish"]) == 2
    assert "not enabled" in capsys.readouterr().err


def test_cli_init_creates_config(tmp_path, capsys):
    brand_dir = tmp_path / "new-brand"
    brand_dir.mkdir()
    reg = tmp_path / "reg.json"
    reg.write_text(json.dumps({"new-brand": str(brand_dir)}))
    assert cli.main(["--brand", "new-brand", "--registry", str(reg), "init"]) == 0
    cfg = yaml.safe_load((brand_dir / "instagram" / "config.yaml").read_text())
    assert cfg["enabled"] is False and cfg["api"]["mode"] == "dry_run"
    assert (brand_dir / "instagram" / "facts.md").exists()
