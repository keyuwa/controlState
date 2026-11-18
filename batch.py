#!/usr/bin/env python3
"""Batch executor: run every scenario in a directory sequentially and
summarize verdicts, timeouts and infrastructure errors."""
import argparse
import json
from collections import Counter
from pathlib import Path

from runner import Runner
from oracle import judge

ROOT = Path(__file__).resolve().parent


def batch(scenario_dir, pattern="*.json", limit=None):
    files = sorted(Path(scenario_dir).glob(pattern))
    if limit:
        files = files[:limit]
    summary = []
    for f in files:
        if f.name.startswith(("report", "replay")):
            continue
        scenario = json.loads(f.read_text())
        try:
            manifest = Runner(scenario).run()
        except Exception as exc:  # infrastructure-level failure
            summary.append({"scenario": scenario["scenario_id"],
                            "verdict": "environment_error",
                            "rationale": repr(exc)})
            continue
        verdict = judge(ROOT / "runs" / manifest["run_id"])
        (ROOT / "runs" / manifest["run_id"] / "verdict.json").write_text(
            json.dumps(verdict, indent=2, ensure_ascii=False))
        summary.append({"scenario": scenario["scenario_id"],
                        "run_id": manifest["run_id"],
                        "verdict": verdict["verdict"],
                        "policy_id": verdict.get("policy_id"),
                        "rationale": verdict["rationale"][:120],
                        "restore_errors": manifest["restore_errors"]})
        print(f"{scenario['scenario_id']}: {verdict['verdict']}")
    tally = Counter(s["verdict"] for s in summary)
    out = {"total": len(summary), "tally": dict(tally), "runs": summary}
    outpath = ROOT / "batch_summary.json"
    outpath.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(json.dumps(out["tally"]))
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dir", type=Path)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    batch(args.dir, limit=args.limit)


if __name__ == "__main__":
    main()
