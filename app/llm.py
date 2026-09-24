"""Thin wrapper over the Anthropic SDK. Every AI loop in the app goes through here."""
import json

import anthropic
import jsonschema

from auth import get_secret

DEFAULT_MODEL = "claude-sonnet-5"


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
              max_tokens: int = 16000, constrained: bool = True) -> dict:
    """One model call returning an object that matches `schema`.

    constrained=True uses structured outputs (grammar-constrained decoding). Large schemas exceed the
    grammar limits, so constrained=False puts the schema in the prompt and validates the reply against
    it in Python, retrying once with the validation errors."""
    if constrained:
        text = _call(system, messages, effort, max_tokens,
                     {"format": {"type": "json_schema", "schema": schema}})
        return json.loads(text)
    sys_prompt = (system + "\n\nRespond with ONE JSON object and nothing else (no prose, no code fences). "
                  "It must validate against this JSON Schema:\n" + json.dumps(schema))
    text = _call(sys_prompt, messages, effort, max_tokens)
    try:
        obj = _parse(text)
        jsonschema.validate(obj, schema)
        return obj
    except (json.JSONDecodeError, jsonschema.ValidationError) as e:
        err = e.message if isinstance(e, jsonschema.ValidationError) else str(e)
        retry = messages + [{"role": "assistant", "content": text},
                            {"role": "user", "content": f"That JSON is invalid: {err[:800]}. Return the corrected "
                                                        "complete JSON object only."}]
        obj = _parse(_call(sys_prompt, retry, effort, max_tokens))
        try:
            jsonschema.validate(obj, schema)
        except jsonschema.ValidationError as e2:
            raise LLMError(f"Model output did not match the schema: {e2.message[:300]}") from e2
        return obj


def _parse(text: str) -> dict:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1].rsplit("```", 1)[0]
    start, end = t.find("{"), t.rfind("}")
    return json.loads(t[start:end + 1])


def _call(system: str, messages: list[dict], effort: str, max_tokens: int, extra_output: dict | None = None) -> str:
    try:
        with client().messages.stream(
            model=model_name(),
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            output_config={"effort": effort, **(extra_output or {})},
        ) as stream:
            resp = stream.get_final_message()
    except anthropic.BadRequestError as e:
        if "credit balance is too low" in str(e.message):
            raise LLMError("The Anthropic account is out of API credit — top it up in the Claude Console "
                           "(Settings → Billing), then try again.") from e
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
    text = "".join(b.text for b in resp.content if b.type == "text")
    if not text:
        raise LLMError("The model returned no content.")
    return text
