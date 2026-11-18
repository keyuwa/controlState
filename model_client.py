#!/usr/bin/env python3
"""Model client for LLM-assisted test generation.

Configuration via environment (never logged):
  MODEL_BASE_URL  OpenAI-compatible endpoint (e.g. http://localhost:11434/v1
                  or https://api.deepseek.com/v1)
  MODEL_API_KEY   API key (sent as Bearer; never persisted)
  MODEL_NAME      model name

Outputs are constrained to the scenario JSON format and validated; invalid
outputs are recorded and retried a limited number of times.
"""
import json
import os
import re
import subprocess
import time
from pathlib import Path

MAX_RETRIES = 3
ROOT = Path(__file__).resolve().parent
CALL_LOG = ROOT / "model_calls.jsonl"


class ModelError(Exception):
    pass


class ModelClient:
    def __init__(self):
        self.base_url = os.environ.get("MODEL_BASE_URL")
        self.api_key = os.environ.get("MODEL_API_KEY")
        self.model = os.environ.get("MODEL_NAME")
        if not (self.base_url and self.model):
            raise ModelError(
                "MODEL_BASE_URL / MODEL_NAME not configured; model-assisted "
                "generation stays in pending-acceptance state")

    def chat(self, messages, temperature=0.7, max_tokens=2000):
        body = {
            "model": self.model, "messages": messages,
            "temperature": temperature, "max_tokens": max_tokens,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key or 'none'}"
        }

        t0 = time.monotonic()
        # Use curl to bypass Python SSL issues with this endpoint
        result = subprocess.run([
            'curl', '-k', '-X', 'POST',
            self.base_url.rstrip("/") + "/chat/completions",
            '-H', f'Content-Type: {headers["Content-Type"]}',
            '-H', f'Authorization: {headers["Authorization"]}',
            '-d', json.dumps(body),
            '--max-time', '120', '--silent', '--show-error'
        ], capture_output=True, text=True)

        if result.returncode != 0 or not result.stdout:
            raise ModelError(f"curl failed (rc={result.returncode}): {result.stderr}")

        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            raise ModelError(f"Invalid JSON from endpoint: {e}, got: {result.stdout[:200]}")
        elapsed = time.monotonic() - t0
        text = payload["choices"][0]["message"]["content"]
        usage = payload.get("usage", {})
        CALL_LOG.open("a").write(json.dumps({
            "ts": time.time(), "model": self.model,
            "elapsed_s": round(elapsed, 2),
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "n_messages": len(messages)}) + "\n")
        return text

    def generate_scenario(self, system_prompt, user_prompt, validate):
        """Ask for one scenario; extract JSON; validate. Limited retries."""
        attempts = []
        for i in range(MAX_RETRIES):
            raw = self.chat([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}])
            try:
                text = raw
                m = re.search(r"\{.*\}", raw, re.DOTALL)
                if m:
                    text = m.group(0)
                scenario = json.loads(text)
            except json.JSONDecodeError as exc:
                attempts.append({"error": f"json: {exc}", "raw_head": raw[:200]})
                continue
            err = validate(scenario)
            if err:
                attempts.append({"error": f"validate: {err}",
                                 "scenario": scenario.get("scenario_id")})
                user_prompt = (user_prompt +
                               f"\n\n上次输出被拒绝：{err}。请修正后只输出 JSON。")
                continue
            scenario["generation"] = {
                "source": "model", "model": self.model, "attempts": i + 1}
            return scenario, attempts
        return None, attempts


SYSTEM_PROMPT = """你是卫星控制安全测试序列生成器。只输出一个 JSON 对象，格式：
{
  "scenario_id": "model_<slug>",
  "initial_state": {"queue": "ENABLED", "heater": 0},
  "steps": [ {"step_id": "s1", "action": <action>, ...}, ... ],
  "expected": {"heater": <0|1|"unchanged">, "verdict": <"pass"|"policy_violation">}
}
可用动作：issue_command(command=/SERVICE-EPS/SetHeater, args={Mode:0|1}, timeout秒)、
hold_queue、release_queue、disable_queue、cancel_pending、read_state、query_eps、wait(seconds)。
硬约束：SetHeater 的 Mode 只能取 0 或 1；禁止 shell/代码；禁止臆造其他命令或参数。
"""


def build_feedback_prompt(context):
    """context: dict with covered_states, rejected_reasons, run_summaries,
    candidate_findings."""
    return (
        "已覆盖的状态/组合：\n" +
        json.dumps(context.get("covered_states", []), ensure_ascii=False) +
        "\n\n被拒绝的序列及原因：\n" +
        json.dumps(context.get("rejected_reasons", [])[-10:], ensure_ascii=False) +
        "\n\n最近执行结果：\n" +
        json.dumps(context.get("run_summaries", [])[-10:], ensure_ascii=False) +
        "\n\n候选异常摘要：\n" +
        json.dumps(context.get("candidate_findings", []), ensure_ascii=False) +
        "\n\n请生成一个此前未覆盖、判定依据明确的新测试序列，只输出 JSON。"
    )
