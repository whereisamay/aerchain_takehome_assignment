"""Thin wrapper over the Anthropic SDK. Every AI loop in the app goes through here."""
import json

import anthropic

from auth import get_secret

DEFAULT_MODEL = "claude-opus-5"


class LLMError(RuntimeError):
    pass


def model_name() -> str:
    return get_secret("ANTHROPIC_MODEL") or DEFAULT_MODEL


def _api_key() -> str:
    """API keys never contain whitespace, so drop any line breaks a paste introduced."""
    return "".join((get_secret("ANTHROPIC_API_KEY") or "").split())


def client() -> anthropic.Anthropic:
    key = _api_key()
    if not key:
        raise LLMError("ANTHROPIC_API_KEY is not configured in secrets.")
    return anthropic.Anthropic(api_key=key)


def json_call(system: str, messages: list[dict], schema: dict, effort: str = "medium",
              max_tokens: int = 16000) -> dict:
    """One model call constrained to a JSON schema. Returns the parsed object."""
    try:
        resp = client().messages.create(
            model=model_name(),
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        )
    except anthropic.BadRequestError as e:
        raise LLMError(f"Model request rejected: {e.message}") from e
    except anthropic.AuthenticationError as e:
        key = _api_key()
        raise LLMError(f"Anthropic rejected the API key (key in secrets starts '{key[:14]}…', "
                       f"{len(key)} characters; a full key is usually 108). Re-copy it from the "
                       "Claude Console into Streamlit secrets.") from e
    except anthropic.RateLimitError as e:
        raise LLMError("Rate limited by the Anthropic API — try again in a moment.") from e
    except anthropic.APIStatusError as e:
        raise LLMError(f"Anthropic API error {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise LLMError("Could not reach the Anthropic API.") from e

    if resp.stop_reason == "refusal":
        raise LLMError("The model declined this request.")
    if resp.stop_reason == "max_tokens":
        raise LLMError("The model ran out of output tokens before finishing.")
    text = next((b.text for b in resp.content if b.type == "text"), None)
    if text is None:
        raise LLMError("The model returned no content.")
    return json.loads(text)
