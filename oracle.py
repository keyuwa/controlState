#!/usr/bin/env python3
"""Independent oracle: judge a run against declared policies.

Reads runs/<run_id>/{events.jsonl,manifest.json}, writes verdict.json.
Verdicts: pass | policy_violation | insufficient_evidence | environment_error.
The oracle never trusts the runner's own step results for policy facts; it
re-derives them from command history acks and telemetry evidence.
"""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent

HEATER_ON_CURRENT_MA = 100  # EPS_CURRENT_OUT > 100mA implies heater active


def load_run(run_dir):
    events = [json.loads(l) for l in open(run_dir / "events.jsonl")]
    manifest = json.loads((run_dir / "manifest.json").read_text())
    return events, manifest


def final_heater_state(events):
    """Infer heater state from the last parameter_read with EPS_CURRENT_OUT."""
    state, at = None, None
    for i, ev in enumerate(events):
        if ev.get("event") == "parameter_read":
            cur = (ev.get("values") or {}).get("/SERVICE-EPS/EPS_CURRENT_OUT")
            if cur and cur.get("value") is not None:
                state = cur["value"] > HEATER_ON_CURRENT_MA
                at = i
    return state, at


def command_outcome(events, command="/SERVICE-EPS/SetHeater"):
    """Most advanced terminal evidence per operator-issued command.

    Control-plane commands (init/restore) are excluded so they cannot be
    mistaken for commands under test.
    """
    outcome = {}
    for ev in events:
        if ev.get("event") == "command_accepted_by_api" and \
                ev.get("command") == command and \
                ev.get("step_id") not in ("init", "restore"):
            outcome[ev["command_id"]] = {"step_id": ev.get("step_id"),
                                         "terminal": None, "acks": {}}
        elif ev.get("event") == "command_terminal":
            o = outcome.get(ev.get("command_id"))
            if o:
                o["terminal"] = ev.get("success")
        elif ev.get("event") == "command_history_entry":
            o = outcome.get(ev.get("command_id"))
            if o and ev.get("acks"):
                merged = o["acks"]
                for name, ack in ev["acks"].items():
                    merged[name] = ack  # later entries override
    return outcome


def judge(run_dir):
    events, manifest = load_run(run_dir)
    scenario = manifest["scenario"]
    expected = scenario.get("expected", {})
    sid = scenario["scenario_id"]

    # -- environment errors ------------------------------------------------
    if manifest.get("run_error"):
        return verdict("environment_error", None,
                       f"run_error: {manifest['run_error']}", events)
    http_fail = [ev for ev in events
                 if isinstance(ev.get("status"), int) and ev["status"] >= 400]
    if http_fail and sid not in ("condition_queue_disabled",):
        ev = http_fail[0]
        return verdict("environment_error", None,
                       f"REST {ev['event']} status {ev['status']}", events)

    heater, heater_at = final_heater_state(events)
    outcome = command_outcome(events)

    def insufficient(why):
        return verdict("insufficient_evidence", expected.get("policy_id"), why, events)

    if heater is None:
        return insufficient("no EPS_CURRENT_OUT telemetry evidence")

    # -- scenario rules -----------------------------------------------------
    if sid == "normal_execution":
        completed = any(o.get("terminal") is True for o in outcome.values())
        if completed and heater is True:
            return verdict("pass", "EXP-2",
                           "command completed and heater effect observed", events)
        if completed and heater is False:
            return verdict("policy_violation", "EXP-2",
                           "command completed but no heater effect", events)
        return insufficient("command did not complete")

    if sid == "cancel_pending":
        rejected = any(
            (o["acks"].get("Acknowledge_Released") or {}).get("status") == "NOK"
            for o in outcome.values())
        released_ok = [cid for cid, o in outcome.items()
                       if (o["acks"].get("Acknowledge_Released") or {}).get("status") == "OK"]
        if released_ok and heater is True:
            return verdict("policy_violation", "EXP-1",
                           f"canceled command(s) {released_ok} released and "
                           "executed after cancel attempt", events)
        if rejected and heater is False:
            return verdict("pass", "EXP-1",
                           "queued command rejected, heater stayed off", events)
        return insufficient(f"rejected={rejected} heater={heater}")

    if sid == "auth_change_revoke":
        user = next((st.get("user") for st in scenario["steps"]
                     if st["action"] == "change_authorization"
                     and st.get("method") == "disable_user"), None)
        revoke_mono = next((ev["t_mono"] for ev in events
                            if ev.get("event") == "user_set_active"
                            and ev.get("target_user") == user), None)
        rel_after = []
        for cid, o in outcome.items():
            rel = o["acks"].get("Acknowledge_Released")
            if rel and rel.get("status") == "OK":
                ev_rel = next((ev for ev in events
                               if ev.get("event") == "command_history_entry"
                               and ev.get("command_id") == cid
                               and "Acknowledge_Released" in (ev.get("acks") or {})), None)
                if revoke_mono and ev_rel and ev_rel["t_mono"] > revoke_mono:
                    rel_after.append(cid)
        if rel_after and heater is True:
            return verdict("policy_violation", "EXP-1",
                           f"command(s) {rel_after} released after revocation of "
                           f"{user} and executed (AUTH-1/2: authorization "
                           "checked only at submission)", events)
        if not rel_after and heater is False:
            return verdict("pass", "EXP-1",
                           "queued command not released after revocation", events)
        return insufficient(f"rel_after_revoke={rel_after} heater={heater}")

    if sid == "condition_queue_disabled":
        denied = any(
            o.get("terminal") is False or
            (o["acks"].get("Acknowledge_Queued") or {}).get("status") == "NOK"
            for o in outcome.values())
        if denied and heater is False:
            return verdict("pass", "QUEUE-1",
                           "queue disabled => command rejected at entry, "
                           "state unchanged", events)
        if heater is True:
            return verdict("policy_violation", "QUEUE-1",
                           "command executed while queue disabled", events)
        return insufficient(f"denied={denied} heater={heater}")

    # -- generic fallback ---------------------------------------------------
    exp_heater = expected.get("heater")
    if exp_heater in (0, 1):
        ok = (heater == bool(exp_heater))
        return verdict("pass" if ok else "policy_violation",
                       expected.get("policy_id"),
                       f"heater={heater} expected={exp_heater}", events)
    return insufficient("no scenario rule matched")


def verdict(v, policy_id, rationale, events):
    return {"verdict": v, "policy_id": policy_id, "rationale": rationale,
            "evidence_events": len(events)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path,
                        help="runs/<run_id> directory")
    args = parser.parse_args()
    result = judge(args.run_dir.resolve())
    (args.run_dir / "verdict.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False))
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
