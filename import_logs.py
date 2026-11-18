#!/usr/bin/env python3
"""Import HoneySat interaction logs from MongoDB and convert to test seeds.

Source: experiment-1 logs_mongodb (localhost:27017), db honeysat_log,
collection ABCMeta (the simulator's Logger collection in this deployment).
data.context distinguishes event types; data is a stringified Python dict.

Origin labels keep data provenance apart:
  - "local_script_experiment": interactions produced by our own runner
  - "honeyd_participant":     interactions from other sessions/agents
Unrecognized requests are preserved raw and flagged as limitations.
"""
import argparse
import ast
import json
from datetime import datetime, timezone
from pathlib import Path

from pymongo import MongoClient

ROOT = Path(__file__).resolve().parent
SEEDS = ROOT / "seeds"

# ZMQ request bytes -> known simulator command
REQUEST_MAP = {
    b"\x01\x00": {"command": "/SERVICE-OBC/GetTemperature", "args": {}},
    b"\x02\x00": {"command": "/SERVICE-EPS/FetchEPSData", "args": {}},
    b"\x03\x00": {"command": "/SERVICE-ADCS/Fetch_ADCS_Mag_Data", "args": {}},
    b"\x03\x01": {"command": "/SERVICE-ADCS/Fetch_ADCS_Gyro_Data", "args": {}},
    b"\x03\x02\x01": {"command": "/SERVICE-ADCS/Fetch_ADCS_Sun_Data", "args": {}},
    b"\xff\x00": {"action": "comm_reachability_probe"},
}
TELEMETRY_CONTEXTS = {"eps_hk", "obs_temp", "gyroscope",
                      "magnetic_field_mGauss", "adcs_sun", "comm_reachable"}


def _norm_bytes(s):
    """Convert logged repr like "b'\\x02\\x02\\x00'" back to bytes."""
    try:
        v = ast.literal_eval(s)
        return v if isinstance(v, bytes) else None
    except Exception:
        return None


def classify(doc):
    raw = doc["data"]
    if isinstance(raw, dict):
        data = raw
    else:
        try:
            data = ast.literal_eval(raw)
        except Exception:
            return None, "unparseable_data"
    ctx = data.get("context")
    req = data.get("request")
    if isinstance(req, str):
        req = _norm_bytes(req)
    elif not isinstance(req, bytes):
        req = None
    if ctx == "eps_set_heater" and "mode" in data:
        return {"command": "/SERVICE-EPS/SetHeater",
                "args": {"Mode": 1 if data["mode"] else 0}}, None
    if ctx == "eps_set_heater" and req and len(req) >= 3:
        return {"command": "/SERVICE-EPS/SetHeater",
                "args": {"Mode": 1 if req[2] > 0 else 0}}, None
    if ctx == "eps_hard_reset":
        return {"action": "eps_hard_reset_native_zmq"}, None
    if req in REQUEST_MAP:
        return dict(REQUEST_MAP[req]), None
    if ctx in TELEMETRY_CONTEXTS:
        return {"action": "telemetry_sample", "context": ctx}, None
    return {"raw_request": str(data.get("request")), "context": ctx}, \
        "unrecognized_command"


def import_logs(since=None, until=None, out=None):
    client = MongoClient("localhost", 27017, username="honeysat_root_1",
                         password="honeysat_rootpass_1234")
    col = client["honeysat_log"]["ABCMeta"]
    q = {}
    if since or until:
        q["time"] = {}
        if since:
            q["time"]["$gte"] = since
        if until:
            q["time"]["$lt"] = until
    docs = list(col.find(q, {"data": 1, "time": 1, "log_type": 1})
                .sort("time", 1).limit(20000))

    seeds, limitations = [], []
    for d in docs:
        action, lim = classify(d)
        if action is None:
            limitations.append({"doc_id": str(d["_id"]), "reason": lim})
            continue
        if lim:
            limitations.append({"doc_id": str(d["_id"]),
                                "reason": lim,
                                "raw": action})
        seeds.append({"time": str(d["time"]), "origin": "local_honeysat_mongodb",
                      "source_doc_id": str(d["_id"]), **action})

    SEEDS.mkdir(exist_ok=True)
    out = out or SEEDS / f"seeds_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    payload = {
        "source": {
            "kind": "local_honeysat_mongodb",
            "uri": "mongodb://localhost:27017",
            "db": "honeysat_log", "collection": "ABCMeta",
            "filter": q, "imported_at": datetime.now(timezone.utc).isoformat(),
            "note": "本机 HoneySat 容器日志；含本实验脚本产生的交互（local_script_experiment 与外部会话未在库内区分，按时间段人工标注）",
        },
        "count": len(seeds),
        "limitations_count": len(limitations),
        "seeds": seeds,
        "limitations": limitations[:50],
    }
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    stats = {}
    for s in seeds:
        key = s.get("command") or s.get("action") or "raw"
        stats[key] = stats.get(key, 0) + 1
    print(json.dumps({"imported": len(seeds), "limitations": len(limitations),
                      "breakdown": stats, "out": str(out)}, indent=2,
                     ensure_ascii=False))
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", default=None, help="e.g. 2026-11-17 12:00")
    parser.add_argument("--until", default=None)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    import_logs(args.since, args.until, Path(args.out) if args.out else None)


if __name__ == "__main__":
    main()
