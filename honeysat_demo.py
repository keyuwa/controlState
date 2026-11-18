"""Validate a local queue release policy using HoneySat's EPS query."""
import argparse
import json
import os
from pathlib import Path
from queue import Queue

from yamcs.client import Credentials, VerificationConfig, YamcsClient

COMMAND = "/SERVICE-EPS/FetchEPSData"
PARAMETER = "/SERVICE-EPS/EPS_VOLTAGE"


SCENARIOS = {"authorized": False, "revoked": True}
EXPECTED = {"authorized": "completed",
            "revoked": "denied_before_yamcs_submission"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-plan", type=Path,
                        help="JSON array ordering the two allowed scenario IDs")
    options = parser.parse_args()
    scenarios = list(SCENARIOS)
    if options.model_plan:
        scenarios = json.loads(options.model_plan.read_text(encoding="utf-8"))
        if (not isinstance(scenarios, list) or len(scenarios) != len(SCENARIOS)
                or any(not isinstance(item, str) for item in scenarios)
                or set(scenarios) != set(SCENARIOS)):
            parser.error("model plan must contain authorized and revoked exactly once")

    credentials = Credentials(
        username=os.environ.get("YAMCS_USERNAME", "administrator"),
        password=os.environ["YAMCS_PASSWORD"],
    )
    client = YamcsClient("localhost:8090", credentials=credentials)
    processor = client.get_processor("satellite", "realtime")
    connection = processor.create_command_connection()
    verification = VerificationConfig()
    verification.disable("Started")

    def run(revoke):
        authorization_epoch = 1
        pending = {"command": COMMAND, "epoch": authorization_epoch}
        evidence = {"queued": pending, "revoked": revoke}
        if revoke:
            authorization_epoch += 1
        evidence["release_epoch"] = authorization_epoch
        if pending["epoch"] != authorization_epoch:
            evidence["result"] = "denied_before_yamcs_submission"
            return evidence

        telemetry = Queue()

        def on_data(delivery):
            for parameter in delivery.parameters:
                telemetry.put({
                    "value": parameter.eng_value,
                    "generation_time": str(parameter.generation_time),
                })

        subscription = processor.create_parameter_subscription(
            [PARAMETER], on_data=on_data, send_from_cache=False,
        )
        try:
            command = connection.issue(COMMAND, args={}, verification=verification)
            command.await_complete(timeout=20)
            if not command.is_success():
                raise RuntimeError(f"Yamcs command failed: {command.error}")
            evidence.update(result="completed", command_id=command.id,
                            telemetry=telemetry.get(timeout=10))
            return evidence
        finally:
            subscription.cancel()

    results = []
    for scenario in scenarios:
        evidence = run(revoke=SCENARIOS[scenario])
        evidence["scenario"] = scenario
        evidence["expected"] = EXPECTED[scenario]
        evidence["passed"] = evidence["result"] == evidence["expected"]
        results.append(evidence)

    report = json.dumps(results, indent=2)
    filename = "honeysat-model-results.json" if options.model_plan else "honeysat-results.json"
    Path(__file__).with_name(filename).write_text(report + "\n", encoding="utf-8")
    print(report)
    if not all(result["passed"] for result in results):
        raise SystemExit("Authorization policy check failed")


if __name__ == "__main__":
    main()
