"""Reading recorded evaluation runs back.

This endpoint reports measurements, so the properties worth pinning are
that it reports them faithfully and that it cannot be talked into reading
anything else off the disk. The results directory is gitignored, so the
tests build their own rather than depending on runs that may not exist.
"""

import json

import pytest
from fastapi.testclient import TestClient

from app.api import evaluation
from app.main import app

client = TestClient(app)

QUESTIONS = {
    "meta": {"corpus": "corpus/corpus.jsonl"},
    "arms": ["full", "no_memory"],
    "summary": {
        "overall": {
            "full": {"n": 60, "correct": 48, "accuracy": 0.8},
            "no_memory": {"n": 60, "correct": 19, "accuracy": 0.317},
        }
    },
    "questions": [{"id": "fact-01", "category": "single_fact"}],
}

SYSTEMS = {
    "measured_at": "2026-09-10T13:36:03+00:00",
    "corpus": {"events": 484, "memories": 199},
    "model_usage": {
        "per_event_ingested": {"cost_usd": 0.000532},
        "models_used": ["gemini-3.1-flash-lite"],
    },
}

TOOLS = {"sections": [{"name": "routing"}], "correct": 22, "total": 23}


@pytest.fixture
def results(tmp_path, monkeypatch):
    """A results directory of our own, so the tests own their data."""
    root = tmp_path / "results"
    root.mkdir()

    (root / "20260910-final").mkdir()
    (root / "20260910-final" / "results.json").write_text(json.dumps(QUESTIONS))
    (root / "20260910-final" / "report.md").write_text("# Evaluation\n")

    (root / "systems-20260910-133603").mkdir()
    (root / "systems-20260910-133603" / "systems.json").write_text(json.dumps(SYSTEMS))

    (root / "tools-20260910-131343").mkdir()
    (root / "tools-20260910-131343" / "tools.json").write_text(json.dumps(TOOLS))

    # An interrupted run, which is a normal thing to find on disk.
    (root / "20260910-105019").mkdir()

    monkeypatch.setattr(evaluation, "RESULTS", root)
    return root


def test_every_kind_of_run_is_listed_with_its_own_headline(results):
    rows = {row["id"]: row for row in client.get("/evaluation/runs").json()["runs"]}

    assert rows["20260910-final"]["kind"] == "questions"
    assert rows["20260910-final"]["headline"]["arms"]["full"]["accuracy"] == 0.8

    assert rows["tools-20260910-131343"]["headline"]["correct"] == 22
    assert rows["systems-20260910-133603"]["headline"]["events"] == 484


def test_an_interrupted_run_is_skipped_rather_than_shown_as_broken(results):
    ids = {row["id"] for row in client.get("/evaluation/runs").json()["runs"]}
    assert "20260910-105019" not in ids


def test_no_runs_at_all_is_a_normal_answer(tmp_path, monkeypatch):
    """A fresh clone has none, and the screen must say so rather than break."""
    monkeypatch.setattr(evaluation, "RESULTS", tmp_path / "nothing-here")
    body = client.get("/evaluation/runs").json()
    assert body["runs"] == []
    assert body["total"] == 0


def test_a_run_comes_back_whole(results):
    body = client.get("/evaluation/runs/20260910-final").json()
    assert body["kind"] == "questions"
    assert body["data"] == QUESTIONS
    assert body["report_md"] == "# Evaluation\n"


def test_the_write_up_is_found_whatever_the_harness_named_it(results):
    """results.json sits beside report.md; systems.json beside systems.md."""
    (results / "systems-20260910-133603" / "systems.md").write_text("# Systems\n")
    body = client.get("/evaluation/runs/systems-20260910-133603").json()
    assert body["report_md"] == "# Systems\n"


def test_a_run_with_no_write_up_says_so_rather_than_failing(results):
    body = client.get("/evaluation/runs/tools-20260910-131343").json()
    assert body["report_md"] is None
    assert body["headline"]["total"] == 23


def test_unreadable_json_is_reported_not_raised(results):
    (results / "broken").mkdir()
    (results / "broken" / "results.json").write_text("{not json")

    rows = {row["id"]: row for row in client.get("/evaluation/runs").json()["runs"]}
    assert rows["broken"]["unreadable"] is True

    assert client.get("/evaluation/runs/broken").status_code == 422


@pytest.mark.parametrize(
    "run_id",
    [
        "..",
        "../secrets",
        "....//....//etc",
        "does-not-exist",
        "20260910-105019",  # exists, but recorded nothing
    ],
)
def test_nothing_outside_the_results_directory_can_be_read(results, run_id):
    """The id is joined onto a path, so it is constrained rather than trusted."""
    assert client.get(f"/evaluation/runs/{run_id}").status_code in (404, 422)
