#!/usr/bin/env python3
"""Minimize a violating sequence by step deletion (ddmin-style greedy).

Re-executes after each removal; keeps the removal only if the same verdict
is reproduced. Saves the minimized scenario under findings/<id>/.
"""
import argparse
import copy
import json
from pathlib import Path

from runner import Runner
from oracle import judge

ROOT = Path(__file__).resolve().parent


def minimize(scenario_path, finding_id, target_verdict="policy_violation",
             max_rounds=3):
    scenario = json.loads(Path(scenario_path).read_text())
    current = copy.deepcopy(scenario)
    current_len = len(current["steps"])
    log = []

    def violates(sc):
        manifest = Runner(sc).run()
        v = judge(ROOT / "runs" / manifest["run_id"])
        (ROOT / "runs" / manifest["run_id"] / "verdict.json").write_text(
            json.dumps(v, indent=2, ensure_ascii=False))
        return v["verdict"] == target_verdict, manifest["run_id"], v

    ok, rid, v = violates(current)
    if not ok:
        print(f"baseline does not violate ({v['verdict']}); nothing to do")
        return None
    log.append({"action": "baseline", "run_id": rid, "verdict": v["verdict"],
                "len": current_len})

    for rnd in range(max_rounds):
        reduced = False
        for i in range(len(current["steps"])):
            candidate = copy.deepcopy(current)
            removed = candidate["steps"].pop(i)
            # never remove the sole command that carries the effect
            if not any(s["action"] == "issue_command" for s in candidate["steps"]):
                continue
            ok, rid, v = violates(candidate)
            log.append({"action": f"remove {removed['step_id']}",
                        "kept": ok, "run_id": rid,
                        "verdict": v["verdict"],
                        "len": len(candidate["steps"])})
            if ok:
                current = candidate
                reduced = True
                break
        if not reduced:
            break

    outdir = ROOT / "findings" / finding_id
    outdir.mkdir(parents=True, exist_ok=True)
    current["scenario_id"] = f"{scenario['scenario_id']}_min"
    current["minimized_from"] = scenario_path.name
    (outdir / "minimized_scenario.json").write_text(
        json.dumps(current, indent=2, ensure_ascii=False))
    (outdir / "minimize_log.json").write_text(
        json.dumps(log, indent=2, ensure_ascii=False))
    print(f"minimized {current_len} -> {len(current['steps'])} steps; "
          f"saved to {outdir}")
    return current


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--finding-id", required=True)
    parser.add_argument("--target-verdict", default="policy_violation")
    args = parser.parse_args()
    minimize(args.scenario, args.finding_id, args.target_verdict)


if __name__ == "__main__":
    main()
