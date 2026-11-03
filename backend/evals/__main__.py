"""python -m evals  ->  run every scenario offline, write the table, print a summary (exit 1 on any failure)."""
import sys
from pathlib import Path

import evals.scenarios  # noqa: F401
from evals.harness import render_table, run_all, write_report

OUT = Path(__file__).resolve().parent / "results"


def main() -> int:
    results = run_all()
    write_report(results, OUT)
    print(render_table(results))
    failed = [r for r in results.values() if not r.passed]
    for r in failed:
        print(f"FAILED {r.id}:\n{r.failure_text()}\n", file=sys.stderr)
    print(f"{len(results) - len(failed)}/{len(results)} passed. Table: {OUT / 'EVAL_TABLE.md'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
