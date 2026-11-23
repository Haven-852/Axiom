"""Direct model calls for ASE agents that are not the coding runtime.

This module must not import openhands. The coding agent goes through the adapter.
"""

import json
import time
from typing import Any

import httpx

from ase.config import Settings
from ase.contracts.domain import DiagnosisResult
from ase.infra.redact import redact


def secret_value(settings: Settings) -> str:
    value = settings.llm_api_key.get_secret_value()
    return value.strip()


def model_log(settings: Settings, *, started: float, usage: dict[str, Any] | None, error: str = "") -> dict[str, Any]:
    secret = secret_value(settings)
    return {
        "provider": settings.llm_provider,
        "model": settings.llm_model,
        "api_base": settings.llm_api_base,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "usage": usage or {},
        "error": redact(error, secret),
    }


def complete(settings: Settings, system: str, user: str, json_mode: bool = False) -> tuple[str, dict[str, Any]]:
    key = secret_value(settings)
    if not key:
        raise RuntimeError("ASE_LLM_API_KEY is empty")
    started = time.perf_counter()
    payload = {
        "model": settings.llm_model,
        "temperature": settings.llm_temperature,
        "max_tokens": settings.llm_max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    try:
        response = httpx.post(
            settings.llm_api_base.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=payload,
            timeout=settings.llm_timeout,
        )
        response.raise_for_status()
        body = response.json()
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(f"model HTTP {exc.response.status_code}") from exc
    except Exception as exc:
        raise RuntimeError(type(exc).__name__) from exc
    message = body["choices"][0]["message"]["content"]
    usage = body.get("usage") or {}
    log = model_log(settings, started=started, usage=usage)
    log["finish_reason"] = body["choices"][0].get("finish_reason")
    if key in json.dumps(log) or key in message:
        raise RuntimeError("model log contained the API key")
    return message, log


def diagnose_failure(settings: Settings, output: str) -> DiagnosisResult:
    rules = _rules(output)
    if not secret_value(settings):
        return rules
    system = (
        "你是测试诊断器。只返回 JSON，对象包含 cause 和 repair_plan 两个字符串。"
        "不要返回 Markdown。修复建议必须限制在当前项目文件内，禁止访问宿主机密钥、Docker socket 和主分支。"
    )
    try:
        text, _log = complete(settings, system, output[-6000:])
    except Exception:
        return rules
    parsed = _parse_json(text)
    if parsed is None:
        return rules
    return DiagnosisResult(cause=parsed["cause"], repair_plan=parsed["repair_plan"], source="model")


def _rules(output: str) -> DiagnosisResult:
    tail = "\n".join(output.strip().splitlines()[-12:])
    return DiagnosisResult(
        cause="pytest 退出码非 0",
        repair_plan="根据下面的测试输出修复实现，不要扩大改动范围。\n" + tail,
        source="rules",
    )


def _parse_json(text: str) -> dict[str, str] | None:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.removeprefix("json").strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("cause"), str) or not isinstance(data.get("repair_plan"), str):
        return None
    return {"cause": data["cause"], "repair_plan": data["repair_plan"]}
