"""ci/scaling_failure_alert.py: one issue for a streak of failed scheduled runs (#573 B9)."""

from __future__ import annotations

import json
from collections.abc import Sequence

import pytest

import scaling_failure_alert as alert


def runner_with(
    listing: list[dict[str, object]], calls: list[list[str]], jobs: list[dict[str, object]]
) -> alert.Runner:
    def runner(cmd: Sequence[str]) -> str:
        calls.append(list(cmd))
        if cmd[0] == "run":
            return json.dumps({"jobs": jobs})
        if cmd[:2] == ["issue", "list"]:
            return json.dumps(listing)
        return ""

    return runner


JOBS: list[dict[str, object]] = [
    {"name": "build", "conclusion": "success"},
    {"name": "assemble", "conclusion": "failure"},
    {"name": "measure", "conclusion": "timed_out"},
    {"name": "deploy-pages", "conclusion": "skipped"},
]


def test_only_unsuccessful_jobs_are_listed() -> None:
    assert alert.failed_jobs({"jobs": JOBS}) == ["assemble (failure)", "measure (timed_out)"]
    assert alert.failed_jobs({"jobs": "nope"}) == []


def test_the_first_failure_creates_the_issue_with_the_jobs_and_the_link() -> None:
    calls: list[list[str]] = []
    done = alert.alert("o/r", "4242", runner_with([], calls, JOBS))
    assert done == "created the alert issue"
    create = calls[-1]
    assert create[:2] == ["issue", "create"] and alert.TITLE in create
    body = create[-1]
    assert "https://github.com/o/r/actions/runs/4242" in body and "assemble (failure)" in body


def test_a_streak_of_failures_comments_on_the_one_open_issue() -> None:
    calls: list[list[str]] = []
    open_issues: list[dict[str, object]] = [
        {"number": 11, "title": "unrelated"},
        {"number": 12, "title": alert.TITLE},
    ]
    assert alert.alert("o/r", "4243", runner_with(open_issues, calls, JOBS)) == "commented on #12"
    assert calls[-1][:3] == ["issue", "comment", "12"]
    assert not any(c[:2] == ["issue", "create"] for c in calls)


def test_dry_run_writes_nothing() -> None:
    calls: list[list[str]] = []
    alert.alert("o/r", "4244", runner_with([], calls, JOBS), dry_run=True)
    assert all(c[0] == "run" or c[:2] == ["issue", "list"] for c in calls)


def test_a_long_job_list_is_capped() -> None:
    many = [{"name": f"job{i}", "conclusion": "failure"} for i in range(40)]
    body = alert.message("o/r", "1", alert.failed_jobs({"jobs": many}))
    assert body.count("\n- job") == alert.MAX_JOBS_LISTED
    assert "and 28 more" in body


def test_selftest_passes() -> None:
    assert alert.selftest() == 0


def test_the_workflow_calls_it_only_for_scheduled_failures() -> None:
    from pathlib import Path

    import yaml

    workflow = yaml.safe_load(
        (
            Path(__file__).resolve().parents[2] / ".github/workflows/benchmark-scaling.yml"
        ).read_text()
    )
    job = workflow["jobs"]["alert"]
    assert "github.event_name == 'schedule'" in job["if"]
    assert job["permissions"] == {"contents": "read", "issues": "write", "actions": "read"}
    with pytest.raises(KeyError):
        _ = job["strategy"]
