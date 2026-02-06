import json
import os
import urllib.request
import urllib.error

# Test metadata (used by run_all.py)
TAGS = ["network", "api"]
TIMEOUT = 60


DEFAULT_PROXY_URL = "https://proxy.notdiamond.ai/v1/proxy/chat/completions"


def _load_models():
    raw_models = os.environ.get("NOTDIAMOND_MODELS")
    if not raw_models:
        return ["gpt-4o-mini"]
    return [model.strip() for model in raw_models.split(",") if model.strip()]


def test():
    """Main test entry point."""
    api_key = os.environ.get("NOTDIAMOND_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Missing NOTDIAMOND_API_KEY. Set it as an environment variable."
        )

    proxy_url = os.environ.get("NOTDIAMOND_PROXY_URL", DEFAULT_PROXY_URL)
    models = _load_models()
    if not models:
        raise RuntimeError(
            "No models provided. Set NOTDIAMOND_MODELS to a comma-separated list."
        )

    payload = {
        "models": models,
        "messages": [
            {"role": "user", "content": "Reply with: retrace test ok"}
        ],
        "tradeoff": "cost",
    }

    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    request = urllib.request.Request(proxy_url, data=data, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status = response.status
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"NotDiamond request failed with status {exc.code}: {error_body}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"NotDiamond request failed: {exc}") from exc

    response_json = json.loads(body) if body else {}
    if status != 200:
        raise RuntimeError(f"Unexpected status {status}: {response_json}")

    choices = response_json.get("choices", [])
    if not choices:
        raise RuntimeError(f"Missing choices in response: {response_json}")

    print("NotDiamond proxy response ok.")
    print("Model candidates:", models)
    print("First choice:", choices[0])


if __name__ == "__main__":
    test()