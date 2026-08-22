# Path: tests/test_deterministic_run_journal.py
# Purpose: Verify bounded deterministic run summaries and trend comparisons.

from pathlib import Path

from tools.deterministic_run_journal import collect_runs, render, summarize, trend


def _run(start: str, target: str, *events: str) -> str:
    lines = [
        f"{start} RUN START: command=research target={target} surface=nauvis force=player"
    ]
    minute = int(start[14:16])
    for index, event in enumerate(events, start=1):
        timestamp = start[:14] + f"{minute + index:02d}" + start[16:]
        lines.append(f"{timestamp} {event}")
    return "\n".join(lines) + "\n"


def test_collects_deduplicated_runs_from_live_and_archive(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    archive.mkdir()
    first = _run(
        "2026-08-22T10:00:00+05:30",
        "mining-productivity-4",
        "PLATE SOURCE: recorded iron-plate provider at (1, 1)",
        "STUCK: belt supply stalled",
        "RUN END",
    )
    (archive / "autonomous-run-old.log").write_text(first, encoding="utf-8")
    live = tmp_path / "autonomous-run.log"
    live.write_text(
        first
        + _run(
            "2026-08-22T11:00:00+05:30",
            "mining-productivity-4",
            "PLATE SOURCE: recorded iron-plate provider at (1, 1)",
            "PLATE SOURCE: recorded copper-plate provider at (2, 2)",
            "STUCK: landfill unavailable",
            "RUN END",
        ),
        encoding="utf-8",
    )

    runs = collect_runs(live, archive)

    assert len(runs) == 2
    assert runs[-1].fields["surface"] == "nauvis"


def test_trend_reports_new_live_evidence_as_better(tmp_path: Path) -> None:
    live = tmp_path / "autonomous-run.log"
    live.write_text(
        _run(
            "2026-08-22T10:00:00+05:30",
            "mining-productivity-4",
            "PLATE SOURCE: recorded iron-plate provider at (1, 1)",
            "STUCK: copper failed",
            "RUN END",
        )
        + _run(
            "2026-08-22T11:00:00+05:30",
            "mining-productivity-4",
            "PLATE SOURCE: recorded iron-plate provider at (1, 1)",
            "PLATE SOURCE: recorded copper-plate provider at (2, 2)",
            "STUCK: landfill failed",
            "RUN END",
        ),
        encoding="utf-8",
    )
    summaries = [summarize(run) for run in collect_runs(live, tmp_path / "missing")]

    verdict, evidence = trend(summaries[1], summaries[0])

    assert verdict == "better"
    assert "plate:copper-plate" in evidence


def test_render_keeps_only_ten_runs_and_includes_manual_notes(tmp_path: Path) -> None:
    live = tmp_path / "autonomous-run.log"
    live.write_text(
        "".join(
            _run(
                f"2026-08-22T{hour:02d}:00:00+05:30",
                "mining-productivity-4",
                f"STUCK: failure {hour}",
                "RUN END",
            )
            for hour in range(11)
        ),
        encoding="utf-8",
    )
    summaries = [summarize(run) for run in collect_runs(live, tmp_path / "missing")]
    latest_id = summaries[-1].run.run_id

    report = render(
        summaries,
        {latest_id: {"change": "rate budget", "lesson": "worse: lost copper"}},
        limit=10,
    )

    assert "Retaining 10 of at most 10 runs" in report
    assert "rate budget" in report
    assert "worse: lost copper" in report
    assert "08-22 00:00" not in report
