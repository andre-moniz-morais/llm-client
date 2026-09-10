"""Request building and response reading for KIE's chat endpoints.

KIE fronts four different chat protocols, one per upstream vendor, and each
model's documentation says which one it speaks:

``chat_openai``
    OpenAI Chat Completions - ``messages`` in, ``choices[].message.content`` out.
``chat_anthropic``
    Anthropic Messages - ``system`` is a separate field and content is a list
    of typed blocks.
``chat_responses``
    OpenAI Responses - ``input`` instead of ``messages``, ``output`` out.
``chat_gemini``
    Gemini generateContent - ``contents``/``parts`` and a ``systemInstruction``.

Every adapter here takes the same neutral turn list and returns plain text, so
the rest of the chat code does not have to care which vendor answered.
"""

from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_MAX_TOKENS = 4096


@dataclass(slots=True)
class Turn:
    """One message on its way to a model."""

    role: str
    text: str
    image_urls: list[str] = field(default_factory=list)


class UnsupportedProtocol(ValueError):
    """The model's adapter is not a chat protocol we speak."""


# --------------------------------------------------------------------------- #
# Request building
# --------------------------------------------------------------------------- #


def _openai_body(spec, turns: list[Turn], system_prompt: str) -> dict:
    messages: list[dict] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})

    for turn in turns:
        if turn.image_urls:
            content: list[dict] = [{"type": "text", "text": turn.text}]
            content += [
                {"type": "image_url", "image_url": {"url": url}} for url in turn.image_urls
            ]
            messages.append({"role": turn.role, "content": content})
        else:
            messages.append({"role": turn.role, "content": turn.text})

    body: dict = {"messages": messages, "stream": False}
    if spec.sends_model_in_body:
        body["model"] = spec.model_id
    return body


def _anthropic_body(spec, turns: list[Turn], system_prompt: str) -> dict:
    messages = []
    for turn in turns:
        # Anthropic has no system role inside `messages`.
        role = "assistant" if turn.role == "assistant" else "user"
        if turn.image_urls:
            content: list[dict] = [
                {"type": "image", "source": {"type": "url", "url": url}}
                for url in turn.image_urls
            ]
            content.append({"type": "text", "text": turn.text})
            messages.append({"role": role, "content": content})
        else:
            messages.append({"role": role, "content": turn.text})

    body: dict = {
        "model": spec.model_id,
        "messages": messages,
        "max_tokens": DEFAULT_MAX_TOKENS,
        "stream": False,
    }
    if system_prompt:
        body["system"] = system_prompt
    return body


def _responses_body(spec, turns: list[Turn], system_prompt: str) -> dict:
    entries: list[dict] = []
    if system_prompt:
        entries.append(
            {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]}
        )

    for turn in turns:
        text_type = "output_text" if turn.role == "assistant" else "input_text"
        content: list[dict] = [{"type": text_type, "text": turn.text}]
        if turn.role != "assistant":
            content += [{"type": "input_image", "image_url": url} for url in turn.image_urls]
        entries.append({"role": turn.role, "content": content})

    return {"model": spec.model_id, "input": entries, "stream": False}


def _gemini_body(spec, turns: list[Turn], system_prompt: str) -> dict:
    contents = []
    for turn in turns:
        parts: list[dict] = [{"text": turn.text}]
        # Gemini reads remote images through fileData rather than a URL string.
        parts += [
            {"fileData": {"fileUri": url, "mimeType": "image/*"}} for url in turn.image_urls
        ]
        contents.append({"role": "model" if turn.role == "assistant" else "user", "parts": parts})

    body: dict = {"contents": contents, "stream": False}
    if system_prompt:
        body["systemInstruction"] = {"parts": [{"text": system_prompt}]}
    return body


BUILDERS = {
    "chat_openai": _openai_body,
    "chat_anthropic": _anthropic_body,
    "chat_responses": _responses_body,
    "chat_gemini": _gemini_body,
}


def build_request(spec, turns: list[Turn], system_prompt: str = "") -> dict:
    builder = BUILDERS.get(spec.adapter)
    if builder is None:
        raise UnsupportedProtocol(f"{spec.adapter} is not a chat protocol.")
    return builder(spec, turns, system_prompt)


# --------------------------------------------------------------------------- #
# Response reading
# --------------------------------------------------------------------------- #


def _join(parts: list[str]) -> str:
    return "\n".join(part for part in parts if part).strip()


def _read_openai(payload: dict) -> str:
    parts = []
    for choice in payload.get("choices") or []:
        message = choice.get("message") or choice.get("delta") or {}
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            parts += [
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") in {"text", "output_text"}
            ]
    return _join(parts)


def _read_anthropic(payload: dict) -> str:
    blocks = payload.get("content")
    if isinstance(blocks, str):
        return blocks.strip()
    parts = [
        block.get("text", "")
        for block in blocks or []
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return _join(parts)


def _read_responses(payload: dict) -> str:
    # The Responses API offers a flattened field when the answer is pure text.
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    if isinstance(direct, list):
        joined = _join([item for item in direct if isinstance(item, str)])
        if joined:
            return joined

    parts = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict) or item.get("type") not in {None, "message"}:
            continue
        for block in item.get("content") or []:
            if isinstance(block, dict) and block.get("type") in {"output_text", "text"}:
                parts.append(block.get("text", ""))
    return _join(parts)


def _read_gemini(payload: dict) -> str:
    parts = []
    for candidate in payload.get("candidates") or []:
        content = candidate.get("content") or {}
        for part in content.get("parts") or []:
            # `thought` parts are the model's reasoning, not its answer.
            if isinstance(part, dict) and part.get("text") and not part.get("thought"):
                parts.append(part["text"])
    return _join(parts)


READERS = {
    "chat_openai": _read_openai,
    "chat_anthropic": _read_anthropic,
    "chat_responses": _read_responses,
    "chat_gemini": _read_gemini,
}


def read_reply(spec, payload: dict) -> str:
    reader = READERS.get(spec.adapter)
    if reader is None:
        raise UnsupportedProtocol(f"{spec.adapter} is not a chat protocol.")
    if not isinstance(payload, dict):
        return ""

    text = reader(payload)
    if text:
        return text

    # Some routes wrap the vendor payload one level deeper.
    for key in ("data", "response", "result"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            text = reader(nested)
            if text:
                return text
    return ""


def read_usage(payload: dict) -> dict:
    """Token accounting, normalised across vendors."""
    if not isinstance(payload, dict):
        return {}
    usage = payload.get("usage") or payload.get("usageMetadata") or {}
    if not isinstance(usage, dict):
        return {}

    def pick(*names: str) -> int | None:
        for name in names:
            value = usage.get(name)
            if isinstance(value, (int, float)):
                return int(value)
        return None

    normalised = {
        "input_tokens": pick("prompt_tokens", "input_tokens", "promptTokenCount"),
        "output_tokens": pick("completion_tokens", "output_tokens", "candidatesTokenCount"),
        "total_tokens": pick("total_tokens", "totalTokenCount"),
    }
    return {key: value for key, value in normalised.items() if value is not None}
