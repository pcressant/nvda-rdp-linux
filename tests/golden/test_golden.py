"""Compare the client and server behaviour with the recorded golden transcripts."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import GOLDEN_DIR, dump, run_scenario
from server_harness import run_server_scenario, unavailable_reason

RUNNERS = {"client": run_scenario, "server": run_server_scenario}

SCENARIOS = sorted((GOLDEN_DIR / "scenarios").glob("*/*.json"))


@pytest.mark.golden
@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda path: f"{path.parent.name}/{path.stem}")
def test_golden_transcript(scenario: Path) -> None:
    if scenario.parent.name == "server" and (
        reason := unavailable_reason(json.loads(scenario.read_text()))
    ):
        pytest.skip(reason)
    expected = GOLDEN_DIR / "expected" / scenario.parent.name / f"{scenario.stem}.txt"
    assert expected.exists(), "no golden transcript; record it with tests/golden/record.py"
    assert dump(RUNNERS[scenario.parent.name](scenario)) == expected.read_text()
