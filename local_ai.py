"""Local-only Ollama integration for AI inventory forecasting and assistance."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from urllib.request import HTTPRedirectHandler


DEFAULT_MODEL = "qwen2.5:1.5b"
DEFAULT_BASE_URL = "http://127.0.0.1:11434"


class LocalAIError(RuntimeError):
    """The local AI service could not provide a usable response."""


def _local_base_url() -> str:
    base_url = os.environ.get("OLLAMA_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    parsed = urllib.parse.urlsplit(base_url)
    if (parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise LocalAIError("For privacy, the AI model must run on this computer at localhost.")
    return base_url


def configured_model() -> str:
    return os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


def _request(path: str, payload: dict | None = None, timeout: int = 12) -> dict:
    url = f"{_local_base_url()}{path}"
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="GET" if body is None else "POST")
    try:
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, new_url):
                return None

        opener = urllib.request.build_opener(NoRedirect)
        with opener.open(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
        raise LocalAIError(
            f"Local AI is not ready. Install Ollama, run `ollama pull {configured_model()}`, and start Ollama."
        ) from error


def status() -> dict:
    model = configured_model()
    try:
        response = _request("/api/tags", timeout=2)
        names = {str(row.get("name", "")) for row in response.get("models", [])}
        installed = model in names or f"{model}:latest" in names
        return {
            "ready": installed,
            "model": model,
            "message": "Ready" if installed else f"Ollama is running. Download {model} to enable AI forecasts.",
        }
    except LocalAIError:
        return {"ready": False, "model": model, "message": f"Install Ollama and run `ollama pull {model}` to enable local AI."}


def chat(system: str, prompt: str, schema: dict | None = None, timeout: int = 180) -> dict | str:
    payload: dict = {
        "model": configured_model(),
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "stream": False,
        "options": {"temperature": 0.1},
        "keep_alive": "10m",
    }
    if schema is not None:
        payload["format"] = schema
    response = _request("/api/chat", payload, timeout=timeout)
    content = response.get("message", {}).get("content")
    if not isinstance(content, str) or not content.strip():
        raise LocalAIError("The local AI model returned an empty response. Try again or check the selected model.")
    if schema is None:
        return content.strip()
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as error:
        raise LocalAIError("The local AI model did not return valid structured forecast data. Try again.") from error
    if not isinstance(parsed, dict):
        raise LocalAIError("The local AI model returned an unexpected forecast format. Try again.")
    return parsed


def create_forecast(product: dict, base_forecast: dict, horizon: int, baseline_estimate: float | None = None) -> dict:
    months = [str(row["month"]) for row in base_forecast.get("forecast", [])]
    if len(months) != horizon:
        raise LocalAIError("The AI forecast could not be aligned to the selected forecast period.")

    schema = {
        "type": "object",
        "properties": {
            "forecast": {
                "type": "array",
                "minItems": horizon,
                "maxItems": horizon,
                "items": {
                    "type": "object",
                    "properties": {
                        "month": {"type": "string"},
                        "units": {"type": "integer", "minimum": 0, "maximum": 1000000},
                        "range_low": {"type": "integer", "minimum": 0, "maximum": 1000000},
                        "range_high": {"type": "integer", "minimum": 0, "maximum": 1000000},
                        "reason": {"type": "string"},
                    },
                    "required": ["month", "units", "range_low", "range_high", "reason"],
                    "additionalProperties": False,
                },
            },
            "summary": {"type": "string"},
        },
        "required": ["forecast", "summary"],
        "additionalProperties": False,
    }
    context = {
        "product": {
            "name": product.get("part_name"),
            "category": product.get("category"),
            "unit_price": product.get("unit_price"),
            "current_stock": product.get("current_stock"),
            "reorder_level": product.get("reorder_level"),
        },
        "recorded_monthly_history": base_forecast.get("history", []),
        "history_months": base_forecast.get("history_months", 0),
        "selected_data_source": base_forecast.get("source"),
        "user_monthly_estimate": baseline_estimate,
        "months_to_forecast": months,
    }
    system = (
        "You are the local AI demand forecasting engine for an inventory system. "
        "Use only the supplied recorded history, product context, and optional user estimate. "
        "Do not invent events, promotions, seasonality, or missing sales. For sparse data, stay conservative and make planning ranges wide. "
        "Return one forecast row for every requested month in order. Each row needs a non-negative whole-unit estimate, "
        "a range_low no greater than units, a range_high no less than units, and a short evidence-based reason. "
        "Return only the requested JSON object."
    )
    output = chat(system, json.dumps(context, ensure_ascii=False), schema=schema)
    rows = output.get("forecast")
    if not isinstance(rows, list) or len(rows) != horizon:
        raise LocalAIError("The local AI model returned the wrong number of forecast months. Try again.")

    clean_rows = []
    for expected_month, row in zip(months, rows):
        if not isinstance(row, dict) or row.get("month") != expected_month:
            raise LocalAIError("The local AI model returned forecast months out of order. Try again.")
        numbers = [row.get("units"), row.get("range_low"), row.get("range_high")]
        if any(isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1_000_000 for value in numbers):
            raise LocalAIError("The local AI model returned invalid unit counts. Try again.")
        units, low, high = numbers
        if not low <= units <= high:
            raise LocalAIError("The local AI model returned an inconsistent planning range. Try again.")
        reason = str(row.get("reason", ""))[:240]
        clean_rows.append({"month": expected_month, "units": units, "range_low": low, "range_high": high, "reason": reason})

    summary = str(output.get("summary", ""))[:500]
    return {"forecast": clean_rows, "summary": summary, "model": configured_model()}
