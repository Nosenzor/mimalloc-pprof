#!/usr/bin/env python3
"""Open or update an issue when a scheduled benchmark-scaling run fails (#573 B9).

The RSS-floor validation broke the daily scaling publication from 2026-09-28 and nothing said so
for over a day: every scheduled run failed in `assemble`, after ~70 minutes of measuring, and the
only signal was a red run nobody was watching. The `alert` job of benchmark-scaling.yml runs this
when a *scheduled* run has a failed job:

  - if an open issue titled TITLE exists, a comment with the run link and the failed jobs is added
    to it (one issue for a streak of failures, not one per day);
  - otherwise the issue is created with the same content.

`gh` does the API work (GH_TOKEN, `issues: write` and `actions: read` on the job). The runner is
injectable so the decision logic is tested without a network.

    python3 ci/scaling_failure_alert.py --repo OWNER/REPO --run-id 123 [--dry-run]
    python3 ci/scaling_failure_alert.py --selftest
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from typing import cast

TITLE = "benchmark-scaling: the scheduled run is failing"
LABEL = "benchmark-failure"
MAX_JOBS_LISTED = 12

Runner = Callable[[Sequence[str]], str]


def gh(cmd: Sequence[str]) -> str:
    return subprocess.run(["gh", *cmd], check=True, capture_output=True, text=True).stdout


def failed_jobs(view: dict[str, object]) -> list[str]:
    """Names of the jobs of a `gh run view --json jobs` document that did not succeed."""
    jobs = view.get("jobs")
    if not isinstance(jobs, list):
        return []
    names: list[str] = []
    for item in cast(list[object], jobs):
        if not isinstance(item, dict):
            continue
        job = cast(dict[str, object], item)
        conclusion = job.get("conclusion")
        if conclusion in ("failure", "timed_out", "cancelled"):
            names.append(f"{job.get('name', '?')} ({conclusion})")
    return names


def message(repo: str, run_id: str, jobs: Sequence[str]) -> str:
    url = f"https://github.com/{repo}/actions/runs/{run_id}"
    listed = (
        "\n".join(f"- {name}" for name in jobs[:MAX_JOBS_LISTED]) or "- (no failed job reported)"
    )
    more = f"\n- ... and {len(jobs) - MAX_JOBS_LISTED} more" if len(jobs) > MAX_JOBS_LISTED else ""
    return (
        f"The scheduled benchmark-scaling run [{run_id}]({url}) failed.\n\n"
        f"Failed jobs:\n{listed}{more}\n\n"
        "A failing scheduled run means the published charts are going stale (the RSS-floor "
        "validation break of 2026-09-28 went unnoticed for over a day, #573). Open the run, read "
        "the failed step, and fix or revert. This issue collects every consecutive failure; close "
        "it when a scheduled run succeeds again."
    )


def alert(repo: str, run_id: str, runner: Runner = gh, dry_run: bool = False) -> str:
    """Comment on the open alert issue, or create it. Returns what was done."""
    view = json.loads(runner(["run", "view", run_id, "--repo", repo, "--json", "jobs"]))
    body = message(repo, run_id, failed_jobs(view))
    listing = json.loads(
        runner(
            [
                "issue", "list", "--repo", repo, "--state", "open", "--search",
                f'"{TITLE}" in:title', "--json", "number,title", "--limit", "20",
            ]
        )
    )  # fmt: skip
    existing = [item for item in listing if item.get("title") == TITLE]
    if existing:
        number = str(existing[0]["number"])
        if not dry_run:
            runner(["issue", "comment", number, "--repo", repo, "--body", body])
        return f"commented on #{number}"
    if not dry_run:
        runner(["issue", "create", "--repo", repo, "--title", TITLE, "--body", body])
    return "created the alert issue"


def selftest() -> int:
    calls: list[list[str]] = []
    jobs_doc = json.dumps(
        {
            "jobs": [
                {"name": "build", "conclusion": "success"},
                {"name": "assemble", "conclusion": "failure"},
                {"name": "measure", "conclusion": "cancelled"},
            ]
        }
    )

    def make(listing: Sequence[Mapping[str, object]]) -> Runner:
        def runner(cmd: Sequence[str]) -> str:
            calls.append(list(cmd))
            if cmd[0] == "run":
                return jobs_doc
            if cmd[:2] == ["issue", "list"]:
                return json.dumps(listing)
            return ""

        return runner

    assert failed_jobs(json.loads(jobs_doc)) == ["assemble (failure)", "measure (cancelled)"]
    assert failed_jobs({}) == []
    assert alert("o/r", "9", make([]), dry_run=False) == "created the alert issue"
    assert calls[-1][:2] == ["issue", "create"] and "assemble (failure)" in calls[-1][-1]
    other = [{"number": 5, "title": "something else"}]
    assert alert("o/r", "9", make(other)) == "created the alert issue"
    mine = [{"number": 7, "title": TITLE}, {"number": 8, "title": TITLE}]
    assert alert("o/r", "9", make(mine)) == "commented on #7"
    assert calls[-1][:3] == ["issue", "comment", "7"]
    before = len(calls)
    assert alert("o/r", "9", make(mine), dry_run=True) == "commented on #7"
    assert all(c[:2] != ["issue", "comment"] for c in calls[before:])
    print("selftest: 7 cases ok")
    return 0


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--repo")
    parser.add_argument("--run-id")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.repo or not args.run_id:
        parser.error("--repo and --run-id are required")
    print(alert(args.repo, args.run_id, dry_run=args.dry_run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
