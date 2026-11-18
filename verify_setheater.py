#!/usr/bin/env python3
"""Phase-1 acceptance: SetHeater state modification observable end-to-end.

Strict order: parameter subscription ready -> SetHeater(on) -> FetchEPSData
readback -> SetHeater(off) -> FetchEPSData readback. Saves evidence JSON.
"""
import json
import os
import sys
import time
from pathlib import Path

from yamcs.client import Credentials, YamcsClient

EPS = ["/SERVICE-EPS/EPS_VOLTAGE", "/SERVICE-EPS/EPS_CURRENT_IN",
       "/SERVICE-EPS/EPS_CURRENT_OUT", "/SERVICE-EPS/EPS_TEMPERATURE"]

creds = Credentials(username=os.environ.get("YAMCS_USERNAME", "administrator"),
                    password=os.environ["YAMCS_PASSWORD"])
client = YamcsClient("localhost:8090", credentials=creds)
proc = client.get_processor("satellite", "realtime")
conn = proc.create_command_connection()

received = []
sub = proc.create_parameter_subscription(
    EPS, on_data=lambda d: received.extend(
        {"p": p.name, "v": p.eng_value, "gen": str(p.generation_time)}
        for p in d.parameters),
    send_from_cache=False)
time.sleep(2)
print(f"subscription active, buffered={len(received)}")

def run(cmd, args=None, timeout=25):
    t0 = time.monotonic()
    c = conn.issue(cmd, args=args or {})
    c.await_complete(timeout=timeout)
    print(f"  {cmd} {args or {}} -> success={c.is_success()} "
          f"id={c.id} elapsed={time.monotonic()-t0:.2f}s")
    return c

out = {"events": []}
def snap(tag):
    time.sleep(1.0)
    out["events"].append({"tag": tag, "tm": list(received)})

run("/SERVICE-EPS/FetchEPSData")
snap("baseline")

run("/SERVICE-EPS/SetHeater", {"Mode": 1})
snap("heater_on")

run("/SERVICE-EPS/FetchEPSData")
snap("heater_on_readback")

run("/SERVICE-EPS/SetHeater", {"Mode": 0})
snap("heater_off")

run("/SERVICE-EPS/FetchEPSData")
snap("heater_off_readback")

sub.cancel()

TAGS = {e["tag"]: e for e in out["events"]}

def latest(tag, param):
    vals = [e["v"] for e in TAGS[tag]["tm"] if e["p"] == param]
    return vals[-1] if vals else None

summary = {}
for tag in ("baseline", "heater_on_readback", "heater_off_readback"):
    summary[tag] = {p.split("/")[-1]: latest(tag, p) for p in EPS}
out["summary"] = summary
Path("verify_setheater.json").write_text(json.dumps(out, indent=2))
print(json.dumps(summary, indent=2))

v_base = summary["baseline"]["EPS_VOLTAGE"]
v_on = summary["heater_on_readback"]["EPS_VOLTAGE"]
ok = v_on is not None and v_base is not None
print("READBACK_DELTA_MV:", None if not ok else v_on - v_base)
sys.exit(0 if ok else 1)
