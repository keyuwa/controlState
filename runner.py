#!/usr/bin/env python3
"""Scenario runner: executes a test sequence against the HoneySat CCSDS stack.

Creates runs/<run_id>/ with events.jsonl (strict monotonic order) and
manifest.json. Restores queue/heater/user state in a finally block.
"""
import argparse
import json
import os
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

from yamcs.client import VerificationConfig

from yamcs_adapter import YamcsAdapter, INSTANCE, PROCESSOR, QUEUE

ROOT = Path(__file__).resolve().parent
EPS_PARAMS = ["/SERVICE-EPS/EPS_VOLTAGE", "/SERVICE-EPS/EPS_CURRENT_IN",
              "/SERVICE-EPS/EPS_CURRENT_OUT", "/SERVICE-EPS/EPS_TEMPERATURE"]

VERIFICATION = VerificationConfig()


class RunError(Exception):
    pass


class Runner:
    def __init__(self, scenario, run_id=None):
        self.scenario = scenario
        self.run_id = run_id or datetime.now(timezone.utc).strftime(
            "%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
        self.run_dir = ROOT / "runs" / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=False)
        self.events_fh = open(self.run_dir / "events.jsonl", "a", buffering=1)
        self.t0 = time.monotonic()
        self.deadline = self.t0 + scenario.get("run_timeout", 600)
        self.issued = {}          # step_id -> issued command object
        self.created_users = []
        self.disabled_users = []
        self.adapter = None       # control plane (queue/IAM): root
        self.operator = None      # command issuing plane: operator under test
        self._extra = {}          # as_user -> adapter
        self._subs = []

    # ------------------------------------------------------------ events
    def emit(self, ev):
        ev.setdefault("run_id", self.run_id)
        self.events_fh.write(json.dumps(ev, default=str) + "\n")

    def check_time(self):
        if time.monotonic() > self.deadline:
            raise RunError("run timeout exceeded")

    # ------------------------------------------------------------ steps
    def step_issue_command(self, step):
        command = step["command"]
        args = step.get("args", {})
        timeout = step.get("timeout", 30)
        if command == "/SERVICE-EPS/SetHeater":
            mode = args.get("Mode")
            if mode not in (0, 1):
                self.emit({"component": "runner", "event": "precheck_reject",
                           "step_id": step["step_id"], "command": command,
                           "reason": f"Mode {mode} out of range {{0,1}} (EXP-2)"})
                return "precheck_reject"
        plane = self.operator
        as_user = step.get("as_user")
        if as_user:
            plane = self._adapter_for(as_user)
        issued = plane.issue(step["step_id"], command, args=args,
                             verification=VERIFICATION,
                             timeout=timeout,
                             expect_complete=False)
        self.issued[step["step_id"]] = issued
        return "completed" if issued.is_success() else "pending_or_failed"

    def step_hold_queue(self, step):
        self.adapter.queue_block(step["step_id"])
        return "completed"

    def step_release_queue(self, step):
        self.adapter.queue_enable(step["step_id"])
        return "completed"

    def step_disable_queue(self, step):
        self.adapter.queue_disable(step["step_id"])
        return "completed"

    def step_cancel_pending(self, step):
        entries = self.adapter.queue_list_commands(step["step_id"])
        mine = [e for e in entries
                if any(str(i.id) == str(e.get("commandId") or e.get("id"))
                       for i in self.issued.values())]
        if not entries:
            self.emit({"component": "runner", "event": "cancel_noop",
                       "step_id": step["step_id"], "reason": "queue empty"})
            return "noop"
        targets = mine or entries  # fallback, flagged in evidence
        if not mine:
            self.emit({"component": "runner", "event": "cancel_foreign_entries",
                       "step_id": step["step_id"],
                       "count": len(targets)})
        for e in targets:
            self.adapter.queue_reject(e, step["step_id"])
        return "completed"

    def step_change_authorization(self, step):
        user = step["user"]
        method = step.get("method", "disable_user")
        if method == "disable_user":
            active = bool(step.get("active", False))
            self.adapter.user_set_active(user, active, step["step_id"])
            if not active and user not in self.disabled_users:
                self.disabled_users.append(user)
            elif active and user in self.disabled_users:
                self.disabled_users.remove(user)
        elif method == "create_user":
            self.adapter.user_create(user, step["password"],
                                     roles=step.get("roles"), step_id=step["step_id"])
            self.created_users.append(user)
        else:
            raise RunError(f"unknown auth method {method}")
        return "completed"

    def _adapter_for(self, as_user):
        """Return an adapter issuing as another user. Password comes from the
        scenario 'users' block: {"name": {"password": ...}}."""
        if as_user in self._extra:
            return self._extra[as_user]
        info = self.scenario.get("users", {}).get(as_user)
        if not info or not info.get("password"):
            raise RunError(f"no credentials for as_user {as_user}")
        adapter = YamcsAdapter(self.emit, username=as_user,
                               password=info["password"])
        self._extra[as_user] = adapter
        return adapter

    def step_query_eps(self, step):
        """Trigger EPS housekeeping downlink so the subscription delivers
        fresh values; settle; later read_state picks them up."""
        self.operator.issue(step["step_id"], "/SERVICE-EPS/FetchEPSData",
                            args={}, verification=VERIFICATION,
                            timeout=step.get("timeout", 30),
                            expect_complete=False)
        time.sleep(step.get("settle", 1.0))
        return "completed"

    def step_read_state(self, step):
        params = step.get("parameters", EPS_PARAMS)
        self.operator.read_parameters(params, step["step_id"])
        self.adapter.queue_list_commands(step["step_id"])
        return "completed"

    def step_wait(self, step):
        time.sleep(step.get("seconds", 1.0))
        return "completed"

    def step_change_mode(self, step):
        # Target (HoneySat CCSDS stack) has no mission-mode concept.
        self.emit({"component": "runner", "event": "unsupported_action",
                   "step_id": step["step_id"], "action": "change_mode",
                   "reason": "target has no mode interface"})
        return "unsupported"

    ACTIONS = {
        "issue_command": step_issue_command,
        "hold_queue": step_hold_queue,
        "release_queue": step_release_queue,
        "disable_queue": step_disable_queue,
        "cancel_pending": step_cancel_pending,
        "change_authorization": step_change_authorization,
        "read_state": step_read_state,
        "query_eps": step_query_eps,
        "wait": step_wait,
        "change_mode": step_change_mode,
    }

    # ------------------------------------------------------------ restore
    def restore(self):
        self.emit({"component": "runner", "event": "restore_begin"})
        errors = []
        try:
            self.adapter.queue_enable()
            self.emit({"component": "runner", "event": "restore_queue_enabled"})
        except Exception as exc:
            errors.append(f"queue_enable: {exc!r}")
        try:
            self.adapter.issue("restore", "/SERVICE-EPS/SetHeater",
                               args={"Mode": 0}, verification=VERIFICATION,
                               timeout=20, expect_complete=False)
            self.emit({"component": "runner", "event": "restore_heater_off"})
        except Exception as exc:
            errors.append(f"heater_off: {exc!r}")
        for user in self.disabled_users:
            try:
                self.adapter.user_set_active(user, True)
            except Exception as exc:
                errors.append(f"reactivate {user}: {exc!r}")
        for user in self.created_users:
            try:
                self.adapter.user_delete(user)
            except Exception as exc:
                errors.append(f"delete {user}: {exc!r}")
        self.emit({"component": "runner", "event": "restore_end",
                   "errors": errors})
        return errors

    # ------------------------------------------------------------ run
    def run(self):
        self.emit({"component": "runner", "event": "run_begin",
                   "scenario_id": self.scenario["scenario_id"],
                   "run_id": self.run_id,
                   "t_utc": datetime.now(timezone.utc).isoformat()})
        step_results, error = {}, None
        try:
            self.adapter = YamcsAdapter(
                self.emit,
                username=os.environ.get("YAMCS_USERNAME", "administrator"),
                password=os.environ["YAMCS_PASSWORD"])
            self.operator = YamcsAdapter(self.emit)
            self._subs.append(self.operator.subscribe_parameters(EPS_PARAMS))
            self._subs.append(self.operator.subscribe_command_history())
            # enforce initial queue state
            init = self.scenario.get("initial_state", {})
            if init.get("queue") == "BLOCKED":
                self.adapter.queue_block("init")
            elif init.get("queue") == "DISABLED":
                self.adapter.queue_disable("init")
            else:
                self.adapter.queue_enable("init")
            if init.get("heater") is not None:
                self.operator.issue("init", "/SERVICE-EPS/SetHeater",
                                    args={"Mode": init["heater"]},
                                    verification=VERIFICATION, timeout=20)

            for step in self.scenario["steps"]:
                self.check_time()
                self.emit({"component": "runner", "event": "step_begin",
                           "step_id": step["step_id"],
                           "action": step["action"]})
                handler = self.ACTIONS[step["action"]]
                try:
                    result = handler(self, step)
                except Exception as exc:
                    result = "step_error"
                    self.emit({"component": "runner", "event": "step_error",
                               "step_id": step["step_id"],
                               "error": repr(exc),
                               "trace": traceback.format_exc()[-800:]})
                step_results[step["step_id"]] = result
                self.emit({"component": "runner", "event": "step_end",
                           "step_id": step["step_id"], "result": result})
        except Exception as exc:
            error = repr(exc)
            self.emit({"component": "runner", "event": "run_error",
                       "error": error})
        finally:
            restore_errors = self.restore() if self.adapter else ["no adapter"]
            for sub in self._subs:
                try:
                    sub.cancel()
                except Exception:
                    pass
            manifest = {
                "run_id": self.run_id,
                "scenario_id": self.scenario["scenario_id"],
                "started_utc": datetime.now(timezone.utc).isoformat(),
                "duration_s": round(time.monotonic() - self.t0, 3),
                "step_results": step_results,
                "run_error": error,
                "restore_errors": restore_errors,
                "scenario": self.scenario,
            }
            (self.run_dir / "manifest.json").write_text(
                json.dumps(manifest, indent=2, default=str))
            self.events_fh.close()
        return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args()
    scenario = json.loads(args.scenario.read_text(encoding="utf-8"))
    manifest = Runner(scenario, run_id=args.run_id).run()
    print(json.dumps({k: manifest[k] for k in
                      ("run_id", "scenario_id", "step_results",
                       "run_error", "restore_errors")}, indent=2))
    sys.exit(1 if manifest["run_error"] else 0)


if __name__ == "__main__":
    main()
