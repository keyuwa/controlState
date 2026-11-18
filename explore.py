#!/usr/bin/env python3
"""Feedback-driven exploration loop (phase 5).

Each round: build context from policies/manifest/prior runs -> model generates
a candidate -> validate -> execute -> oracle verdict -> update novelty store.
Novelty: finite feature vector per run
  (queue_state, heater_state, command_lifecycle_stage, auth_epoch_relation)
timestamps and other high-cardinality fields are excluded by construction.
"""
import argparse
import json
from pathlib import Path

from generator import validate
from model_client import ModelClient, ModelError, SYSTEM_PROMPT, \
    build_feedback_prompt
from runner import Runner
from oracle import judge

ROOT = Path(__file__).resolve().parent
EXPLORE_DIR = ROOT / "explore_runs"


def feature_vector(manifest, verdict):
    steps = manifest["scenario"]["steps"]
    return {
        "queue_ops": sorted(s["action"] for s in steps
                            if "queue" in s["action"]),
        "has_cancel": any(s["action"] == "cancel_pending" for s in steps),
        "has_revoke": any(s["action"] == "change_authorization"
                          for s in steps),
        "modes": sorted(str(s.get("args", {}).get("Mode"))
                        for s in steps if s["action"] == "issue_command"),
        "verdict": verdict["verdict"],
    }


def summarize_run(manifest, verdict):
    return {"scenario": manifest["scenario_id"],
            "run_id": manifest["run_id"],
            "verdict": verdict["verdict"],
            "policy_id": verdict.get("policy_id")}


def explore(rounds, budget_runs=None):
    try:
        client = ModelClient()
    except ModelError as exc:
        print(f"model not configured: {exc}")
        return None

    EXPLORE_DIR.mkdir(exist_ok=True)
    context = {"covered_states": [], "rejected_reasons": [],
               "run_summaries": [], "candidate_findings": []}
    seen_features = set()
    model_calls = 0

    for rnd in range(rounds):
        if budget_runs and model_calls >= budget_runs:
            break
        prompt = build_feedback_prompt(context)
        scenario, attempts = client.generate_scenario(SYSTEM_PROMPT, prompt,
                                                      validate)
        model_calls += 1
        if scenario is None:
            context["rejected_reasons"].append(
                {"round": rnd, "attempts": attempts})
            continue
        fv = json.dumps(feature_vector({"scenario": scenario},
                                       {"verdict": "?", "policy_id": None}),
                        sort_keys=True)
        novel = fv not in seen_features
        scenario["scenario_id"] = f"{scenario['scenario_id']}_r{rnd}"
        manifest = Runner(scenario).run()
        verdict = judge(ROOT / "runs" / manifest["run_id"])
        (ROOT / "runs" / manifest["run_id"] / "verdict.json").write_text(
            json.dumps(verdict, indent=2, ensure_ascii=False))
        entry = summarize_run(manifest, verdict)
        entry["novel"] = novel
        context["run_summaries"].append(entry)
        context["covered_states"].append(fv)
        seen_features.add(fv)
        if verdict["verdict"] == "policy_violation":
            context["candidate_findings"].append(entry)
        (EXPLORE_DIR / f"round_{rnd:03d}.json").write_text(json.dumps(
            {"scenario": scenario, "manifest_run_id": manifest["run_id"],
             "verdict": verdict, "novel": novel, "attempts": attempts},
            indent=2, ensure_ascii=False))
        print(f"round {rnd}: {verdict['verdict']} novel={novel} "
              f"({manifest['run_id']})")

    summary = {"rounds": len(context["run_summaries"]),
               "violations": len(context["candidate_findings"]),
               "model_calls": model_calls,
               "runs": context["run_summaries"]}
    (EXPLORE_DIR / "explore_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False))
    print(json.dumps({k: summary[k] for k in
                      ("rounds", "violations", "model_calls")}, indent=2))
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--budget", type=int, default=None)
    args = parser.parse_args()
    explore(args.rounds, args.budget)


if __name__ == "__main__":
    main()
