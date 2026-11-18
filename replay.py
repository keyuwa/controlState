#!/usr/bin/env python3
"""Replay a scenario N times after state restore; count consistent verdicts.

Usage: python replay.py scenarios/auth_change_revoke.json --times 5
"""
import argparse
import json
from collections import Counter
from pathlib import Path

from runner import Runner
from oracle import judge

ROOT = Path(__file__).resolve().parent


def replay(scenario_path, times, stop_on_mismatch=False):
    scenario = json.loads(Path(scenario_path).read_text())
    results = []
    for i in range(times):
        manifest = Runner(scenario).run()
        verdict = judge(ROOT / "runs" / manifest["run_id"])
        (ROOT / "runs" / manifest["run_id"] / "verdict.json").write_text(
            json.dumps(verdict, indent=2, ensure_ascii=False))
        results.append({"run_id": manifest["run_id"], **verdict,
                        "restore_errors": manifest["restore_errors"]})
        print(f"[{i+1}/{times}] {manifest['run_id']} "
              f"{verdict['verdict']} ({verdict['rationale'][:60]})")
        if stop_on_mismatch and verdict["verdict"] != results[0]["verdict"]:
            break
    tally = Counter(r["verdict"] for r in results)
    summary = {"scenario": scenario["scenario_id"], "times": len(results),
               "tally": dict(tally), "results": results}
    out = ROOT / "findings" / f"replay_{scenario['scenario_id']}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(json.dumps(summary["tally"]))
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--times", type=int, default=5)
    parser.add_argument("--stop-on-mismatch", action="store_true")
    args = parser.parse_args()
    replay(args.scenario, args.times, args.stop_on_mismatch)


if __name__ == "__main__":
    main()
