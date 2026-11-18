#!/usr/bin/env python3
"""Scenario generator: templates + mutations -> validated sequences.

Mutations: argument variation, adjacent-step swap, state-event insertion.
Every generated sequence is validated (schema + semantic preconditions)
before it is written; invalid candidates are counted and reported.
"""
import argparse
import copy
import itertools
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCEN_DIR = ROOT / "generated"

KNOWN_COMMANDS = {
    "/SERVICE-EPS/SetHeater": {"args": {"Mode": {0, 1}}},
    "/SERVICE-EPS/FetchEPSData": {"args": {}},
}
EPS_PARAMS = ["/SERVICE-EPS/EPS_VOLTAGE", "/SERVICE-EPS/EPS_CURRENT_IN",
              "/SERVICE-EPS/EPS_CURRENT_OUT", "/SERVICE-EPS/EPS_TEMPERATURE"]


def validate(sc):
    """Semantic preconditions; returns error string or None."""
    for step in sc["steps"]:
        action = step.get("action")
        if action not in {"issue_command", "hold_queue", "release_queue",
                          "disable_queue", "cancel_pending",
                          "change_authorization", "change_mode",
                          "read_state", "query_eps", "wait"}:
            return f"unknown action {action}"
        if action == "issue_command":
            cmd = step.get("command")
            if cmd not in KNOWN_COMMANDS:
                return f"unknown command {cmd}"
            spec = KNOWN_COMMANDS[cmd]["args"]
            for name, allowed in spec.items():
                if step.get("args", {}).get(name) not in allowed:
                    return f"arg {name}={step.get('args', {}).get(name)} out of range"
            if not isinstance(step.get("timeout", 1), (int, float)):
                return "bad timeout"
    if not any(s["action"] == "issue_command" for s in sc["steps"]):
        return "no command step"
    return None


# ------------------------------------------------------------ mutations
def mutate_args(sc, rng):
    out = copy.deepcopy(sc)
    for step in out["steps"]:
        if step["action"] == "issue_command" and step["command"] == "/SERVICE-EPS/SetHeater":
            step["args"] = {"Mode": rng.choice([0, 1])}
    return out


def swap_adjacent(sc, rng):
    out = copy.deepcopy(sc)
    idx = [i for i in range(len(out["steps"]) - 1)]
    if not idx:
        return out
    i = rng.choice(idx)
    out["steps"][i], out["steps"][i + 1] = out["steps"][i + 1], out["steps"][i]
    return out


def insert_event(sc, rng):
    out = copy.deepcopy(sc)
    pool = [{"action": "wait", "seconds": rng.choice([0.5, 1, 2])},
            {"action": "read_state"},
            {"action": "query_eps"}]
    pos = rng.randint(0, len(out["steps"]))
    ev = copy.deepcopy(rng.choice(pool))
    out["steps"].insert(pos, ev)
    return out


def renumber(sc):
    for i, s in enumerate(sc["steps"], 1):
        s["step_id"] = f"g{i}"
    return sc


def expected_for(sc):
    """Recompute expected outcome conservatively from semantics."""
    mode = None
    queue_disabled_before = False
    canceled = False
    released_after_cancel = False
    saw_disable = False
    blocked = sc.get("initial_state", {}).get("queue") == "BLOCKED"
    for s in sc["steps"]:
        a = s["action"]
        if a == "disable_queue":
            queue_disabled_before = True
        elif a == "release_queue":
            queue_disabled_before = False
            if canceled:
                released_after_cancel = True
            blocked = False
        elif a == "hold_queue":
            blocked = True
        elif a == "issue_command" and s["command"] == "/SERVICE-EPS/SetHeater":
            if not queue_disabled_before:
                mode = s["args"]["Mode"]
            if canceled:
                # command issued after cancel: cancel no longer covers it
                canceled = False
        elif a == "cancel_pending":
            canceled = blocked  # only queued commands are covered
        elif a == "change_authorization" and not s.get("active", True):
            saw_disable = True
    heater = mode if mode is not None else "unchanged"
    # cancel coverage: if canceled while queued and never reissued, effect off
    if canceled and not released_after_cancel:
        heater = 0
    verdict = "pass"
    policy = None
    if saw_disable and blocked:
        # native gap: queued command of a disabled user is still released
        verdict = "policy_violation"
        policy = "EXP-1"
        heater = mode if mode is not None else 1
    sc["expected"] = {"heater": heater, "queue": "ENABLED",
                      "verdict": verdict}
    if policy:
        sc["expected"]["policy_id"] = policy
    return sc


def generate(templates, n, seed):
    rng = random.Random(seed)
    SCEN_DIR.mkdir(exist_ok=True)
    produced, invalid = [], 0
    seen = set()
    bases = [json.loads(p.read_text()) for p in templates]
    tries = 0
    while len(produced) < n and tries < n * 50:
        tries += 1
        base = copy.deepcopy(rng.choice(bases))
        base.pop("users", None)
        for step in base["steps"]:
            step.pop("as_user", None)
        for _ in range(rng.randint(1, 3)):
            op = rng.choice(["args", "swap", "insert"])
            if op == "args":
                base = mutate_args(base, rng)
            elif op == "swap":
                base = swap_adjacent(base, rng)
            else:
                base = insert_event(base, rng)
        base = expected_for(renumber(base))
        base["scenario_id"] = f"gen_{seed}_{len(produced):03d}"
        err = validate(base)
        if err:
            invalid += 1
            continue
        key = json.dumps(base["steps"], sort_keys=True)
        if key in seen:
            invalid += 1
            continue
        seen.add(key)
        produced.append(base)
    for sc in produced:
        (SCEN_DIR / f"{sc['scenario_id']}.json").write_text(
            json.dumps(sc, indent=2, ensure_ascii=False))
    report = {"seed": seed, "requested": n, "generated": len(produced),
              "invalid_or_duplicate": invalid, "dir": str(SCEN_DIR)}
    (SCEN_DIR / f"report_{seed}.json").write_text(
        json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return produced


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--templates", nargs="+",
                        default=[str(p) for p in sorted(
                            (ROOT / "scenarios").glob("*.json"))])
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()
    generate([Path(p) for p in args.templates], args.count, args.seed)


if __name__ == "__main__":
    main()
