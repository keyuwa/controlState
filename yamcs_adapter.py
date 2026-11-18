#!/usr/bin/env python3
"""Yamcs/HoneySat experiment adapter.

Wraps the yamcs-client library and raw REST calls (queue control and IAM,
which yamcs-client 1.12 does not expose). Every externally visible action
emits an event dict to a sink so the runner can persist strict event order.

Causality rules implemented here:
  * parameter subscriptions are created and confirmed receiving BEFORE any
    command of the run is issued (send_from_cache=False);
  * every event carries time.monotonic() and UTC time;
  * queue/IAM actions go through REST and record the HTTP status.
"""
import os
import time
from datetime import datetime, timezone

import requests
from yamcs.client import Credentials, YamcsClient

YAMCS_URL = os.environ.get("YAMCS_URL", "http://localhost:8090")
INSTANCE = "satellite"
PROCESSOR = "realtime"
QUEUE = "default"


def _now():
    return {
        "t_mono": time.monotonic(),
        "t_utc": datetime.now(timezone.utc).isoformat(),
    }


class YamcsAdapter:
    def __init__(self, event_sink, username=None, password=None):
        self.username = username or os.environ.get("YAMCS_USERNAME", "administrator")
        self.password = password or os.environ["YAMCS_PASSWORD"]
        creds = Credentials(username=self.username, password=self.password)
        self.client = YamcsClient(YAMCS_URL.replace("http://", ""), credentials=creds)
        self.processor = self.client.get_processor(INSTANCE, PROCESSOR)
        self.conn = self.processor.create_command_connection()
        self.sink = event_sink
        self._session = requests.Session()
        self._session.auth = (self.username, self.password)
        self._param_values = {}
        self._sub_active = {}

    # ------------------------------------------------------------- events
    def _emit(self, component, event, **kw):
        ev = {"component": component, "event": event, **_now(), **kw}
        self.sink(ev)
        return ev

    # ------------------------------------------------------- subscription
    def subscribe_parameters(self, parameters):
        """Subscribe with cache off; mark subscription ready only after the
        first on-data callback fires (or immediately if a value arrives)."""
        def on_data(delivery):
            for p in delivery.parameters:
                self._param_values[p.name] = {
                    "value": p.eng_value,
                    "generation_time": str(p.generation_time),
                    "receipt_mono": time.monotonic(),
                    "receipt_utc": datetime.now(timezone.utc).isoformat(),
                }
            self._sub_active["params"] = True

        # create_parameter_subscription blocks until the server acknowledges
        # the subscription; that acknowledgement is the ordering barrier that
        # guarantees the subscription precedes any command issued afterwards.
        sub = self.processor.create_parameter_subscription(
            parameters, on_data=on_data, send_from_cache=False
        )
        self._emit("runner", "subscription_ready",
                   parameters=list(parameters), ready=True)
        return sub

    def subscribe_command_history(self):
        """Record command-history entries (queued/released/sent/complete acks)
        for commands issued by anyone, so cross-session commands are visible
        and cannot be silently attributed to this run."""
        def on_entry(entry):
            acks = {name: {"status": ack.status,
                           "time": str(ack.time),
                           "message": ack.message}
                    for name, ack in entry.acknowledgments.items()}
            self._emit("yamcs", "command_history_entry",
                       command_id=str(entry.id),
                       name=entry.name,
                       username=entry.username,
                       acks=acks,
                       complete=entry.attributes.get("CommandComplete_Status"))

        sub = self.processor.create_command_history_subscription(on_data=on_entry)
        self._emit("runner", "cmdhist_subscription_ready")
        return sub

    # ------------------------------------------------------------ command
    def issue(self, step_id, command, args=None, verification=None,
              timeout=30, expect_complete=True):
        self._emit("runner", "command_issue_request", step_id=step_id,
                   command=command, args=args or {})
        t0 = time.monotonic()
        issued = self.conn.issue(command, args=args or {}, verification=verification)
        self._emit("yamcs", "command_accepted_by_api", step_id=step_id,
                   command=command, command_id=issued.id)
        try:
            issued.await_complete(timeout=timeout)
        except Exception as exc:  # e.g. timeout of verification
            self._emit("yamcs", "command_wait_error", step_id=step_id,
                       command_id=issued.id, error=repr(exc))
        ok = issued.is_success() if hasattr(issued, "is_success") else None
        self._emit("yamcs", "command_terminal", step_id=step_id,
                   command_id=issued.id, success=ok,
                   elapsed=time.monotonic() - t0)
        return issued

    def command_history_rest(self, command_name=None, limit=50):
        """Pull command history from the archive REST API for ack evidence."""
        url = f"{YAMCS_URL}/api/archive/{INSTANCE}/commands"
        r = self._session.get(url, params={"limit": limit})
        self._emit("runner", "rest_command_history", status=r.status_code)
        if r.ok:
            return r.json()
        return None

    # -------------------------------------------------------- queue (REST)
    def _queue_post(self, action, step_id=None, command=None):
        url = (f"{YAMCS_URL}/api/processors/{INSTANCE}/{PROCESSOR}"
               f"/queues/{QUEUE}{action}")
        r = self._session.post(url)
        self._emit("yamcs", f"queue_{action.strip(':')}", step_id=step_id,
                   command=command, status=r.status_code,
                   response=r.text[:300] if not r.ok else None)
        r.raise_for_status()
        return r

    def _queue_get(self, path=""):
        url = (f"{YAMCS_URL}/api/processors/{INSTANCE}/{PROCESSOR}"
               f"/queues/{QUEUE}{path}")
        r = self._session.get(url)
        self._emit("yamcs", f"queue_get{path.replace('/', '_')}", status=r.status_code)
        r.raise_for_status()
        return r.json()

    def queue_block(self, step_id=None):
        return self._queue_post(":block", step_id=step_id)

    def queue_enable(self, step_id=None):
        return self._queue_post(":enable", step_id=step_id)

    def queue_disable(self, step_id=None):
        return self._queue_post(":disable", step_id=step_id)

    def queue_list_commands(self, step_id=None):
        data = self._queue_get("/commands")
        return data.get("commands", data.get("entries", []))

    def queue_reject(self, entry, step_id=None):
        cid = entry.get("commandId") or entry.get("id")
        url = (f"{YAMCS_URL}/api/processors/{INSTANCE}/{PROCESSOR}"
               f"/queues/{QUEUE}/commands/{cid}:reject")
        r = self._session.post(url)
        self._emit("yamcs", "queue_reject", step_id=step_id,
                   command_id=str(cid), status=r.status_code,
                   response=r.text[:300] if not r.ok else None)
        r.raise_for_status()

    def queue_accept(self, entry, step_id=None):
        cid = entry.get("commandId") or entry.get("id")
        url = (f"{YAMCS_URL}/api/processors/{INSTANCE}/{PROCESSOR}"
               f"/queues/{QUEUE}/commands/{cid}:accept")
        r = self._session.post(url)
        self._emit("yamcs", "queue_accept", step_id=step_id,
                   command_id=str(cid), status=r.status_code,
                   response=r.text[:300] if not r.ok else None)
        r.raise_for_status()

    # ------------------------------------------------------------- IAM
    def user_set_active(self, username, active, step_id=None):
        url = f"{YAMCS_URL}/api/users/{username}"
        r = self._session.patch(url, json={"active": active})
        self._emit("yamcs", "user_set_active", step_id=step_id,
                   target_user=username, active=active, status=r.status_code)
        r.raise_for_status()

    def user_create(self, username, password, roles=None, step_id=None,
                    active=True):
        # CreateUserRequest only accepts name/password; roles are assigned
        # afterwards via UpdateUserRequest.roleAssignment (PATCH).
        url = f"{YAMCS_URL}/api/users"
        r = self._session.post(url, json={"name": username, "password": password})
        self._emit("yamcs", "user_create", step_id=step_id,
                   target_user=username, status=r.status_code)
        r.raise_for_status()
        if roles:
            self.user_set_roles(username, roles, step_id=step_id)
        if not active:
            self.user_set_active(username, False, step_id=step_id)

    def user_set_roles(self, username, roles, step_id=None):
        url = f"{YAMCS_URL}/api/users/{username}"
        r = self._session.patch(
            url, json={"roleAssignment": {"roles": list(roles)}})
        self._emit("yamcs", "user_set_roles", step_id=step_id,
                   target_user=username, roles=list(roles),
                   status=r.status_code)
        r.raise_for_status()

    def user_delete(self, username, step_id=None):
        url = f"{YAMCS_URL}/api/users/{username}"
        r = self._session.delete(url)
        self._emit("yamcs", "user_delete", step_id=step_id,
                   target_user=username, status=r.status_code)
        r.raise_for_status()

    # ------------------------------------------------------------ params
    def read_parameters(self, names, step_id=None):
        vals = {n: self._param_values.get(n) for n in names}
        self._emit("runner", "parameter_read", step_id=step_id,
                   values=vals)
        return vals


def _cmd_id_str(cmd_id):
    try:
        return f"{cmd_id.generation_time}-{cmd_id.origin}-{cmd_id.sequence_number}"
    except AttributeError:
        return str(cmd_id)
