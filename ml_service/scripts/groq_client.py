import json
import os
import urllib.error
import urllib.request


DEFAULT_GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"


def _groq_api_key() -> str:
    api_key = (os.getenv("GROQ_API_KEY") or os.getenv("API_KEY_STORAGE") or "").strip()
    if not api_key:
        raise RuntimeError("GROQ_API_KEY/API_KEY_STORAGE is not configured.")
    return api_key


def chat_completion(
    *,
    system_prompt: str,
    user_prompt: str,
    model: str,
    max_completion_tokens: int,
    temperature: float = 0.1,
) -> str:
    api_key = _groq_api_key()
    url = os.getenv("GROQ_API_URL", DEFAULT_GROQ_API_URL).strip() or DEFAULT_GROQ_API_URL
    timeout = float(os.getenv("GROQ_TIMEOUT_SEC", "60"))
    payload = {
        "model": model,
        "temperature": temperature,
        "max_completion_tokens": max_completion_tokens,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": os.getenv("GROQ_USER_AGENT", "curl/8.7.1"),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Groq API request failed: {exc}") from exc

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("Groq API returned unexpected payload.") from exc

    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("Groq API returned empty content.")

    return content.strip()
