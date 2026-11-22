"""Record golden transcripts: ``python tests/golden/record.py [names]``.

A name is ``client/<scenario>``, ``server/<scenario>`` or a bare scenario
name, which matches it on both sides.

Recording overwrites ``expected/``.  Only record from a revision whose
behaviour is the reference; any later difference must be reviewed with
``git diff tests/golden/expected`` before being accepted.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import GOLDEN_DIR, dump, run_scenario
from server_harness import run_server_scenario

RUNNERS = {"client": run_scenario, "server": run_server_scenario}


def main(names: list[str]) -> None:
    for scenario in sorted((GOLDEN_DIR / "scenarios").glob("*/*.json")):
        qualified = f"{scenario.parent.name}/{scenario.stem}"
        if names and scenario.stem not in names and qualified not in names:
            continue
        target = GOLDEN_DIR / "expected" / scenario.parent.name / f"{scenario.stem}.txt"
        target.write_text(dump(RUNNERS[scenario.parent.name](scenario)))
        print(f"recorded {target.relative_to(GOLDEN_DIR)}")


if __name__ == "__main__":
    main(sys.argv[1:])
