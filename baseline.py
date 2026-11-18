#!/usr/bin/env python3
"""Internal baseline (phase 7, arms that do not need a model).

Compares under an equal-execution budget:
  - template arm: the four hand-written scenarios
  - random arm:   generator-produced sequences from a fixed seed
Metrics: verdict tally, distinct feature coverage, violations found,
wall-clock cost. Random arm uses a recorded seed for reproducibility.
"""
import argparse
import json
import time
from collections import Counter
from pathlib import Path

from generator import generate
from runner import Runner
from oracle import judge
from explore import feature_vector

ROOT = Path(__file__).resolve().parent


def run_arm(name, scenario_files):
    results = []
    t0 = time.monotonic()
    for f in scenario_files:
        scenario = json.loads(Path(f).read_text())
        manifest = Runner(scenario).run()
        verdict = judge(ROOT / "runs" / manifest["run_id"])
        fv = json.dumps(feature_vector(manifest, verdict), sort_keys=True)
        results.append({"scenario": scenario["scenario_id"],
                        "run_id": manifest["run_id"],
                        "verdict": verdict["verdict"],
                        "policy_id": verdict.get("policy_id"),
                        "features": fv})
    wall = time.monotonic() - t0
    tally = Counter(r["verdict"] for r in results)
    coverage = len({r["features"] for r in results})
    return {"arm": name, "executions": len(results),
            "verdict_tally": dict(tally),
            "distinct_feature_vectors": coverage,
            "violations": [r for r in results if r["verdict"] == "policy_violation"],
            "wall_clock_s": round(wall, 1),
            "results": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--random-count", type=int, default=4)
    args = parser.parse_args()

    templates = sorted((ROOT / "scenarios").glob("*.json"))
    generated = generate(templates, args.random_count, args.seed)
    gen_files = [ROOT / "generated" / f"{s['scenario_id']}.json"
                 for s in generated]

    report = {
        "budget_note": "等执行次数：template 4 vs random %d；随机种子 %d" %
                       (args.random_count, args.seed),
        "template_arm": run_arm("template", templates),
        "random_arm": run_arm("random", gen_files),
    }
    out = ROOT / "baseline_internal.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(json.dumps({k: {kk: report[k][kk] for kk in
                          ("executions", "verdict_tally",
                           "distinct_feature_vectors", "wall_clock_s")}
                      for k in ("template_arm", "random_arm")},
                     indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
