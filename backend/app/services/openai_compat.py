"""Chat completions, for models that disagree about the older parameters.

Newer OpenAI models refuse settings their predecessors took. `gpt-5.6-luna`
answers any `temperature` other than its default with

    400 unsupported_value: 'temperature' does not support 0.2 with this model.
    Only the default (1) value is supported.

and the call sites here ask for 0, 0.1, 0.2 and 0.4 — every one of them a hard
failure the moment the configured model changes.

Hardcoding which model tolerates what would need editing on every release, and
would be wrong for whatever is configured next. So this asks, and listens: it
sends the parameters as written, and when the API names one as unsupported it
drops that parameter and tries again. The refusal is remembered per model, so
a given parameter costs one wasted call for the life of the process rather
than one per request.

Nothing else about the call changes. A model that accepts `temperature` still
gets the value the caller chose.
"""
from __future__ import annotations

import logging
from typing import Any

from openai import BadRequestError

log = logging.getLogger(__name__)

# model -> parameters it has already refused
_REFUSED: dict[str, set[str]] = {}

# The API says an argument is unusable in one of two ways: the parameter is
# unknown, or the value is out of range for this model. Both are answered by
# leaving it out.
_UNSUPPORTED_CODES = {"unsupported_value", "unsupported_parameter"}


def _refused_parameter(err: BadRequestError) -> str | None:
    """The parameter this 400 is complaining about, if that is what it is.

    The SDK exposes `code` and `param` on the exception, which is the reading
    to trust. `body` is a fallback, and it is worth being careful with: it
    holds the error object itself — {"message", "type", "param", "code"} — not
    the {"error": …} envelope the API sends over the wire.
    """
    code = getattr(err, "code", None)
    param = getattr(err, "param", None)

    if code is None or param is None:
        body = getattr(err, "body", None)
        if isinstance(body, dict):
            detail = body.get("error") if isinstance(body.get("error"), dict) else body
            code = code or detail.get("code")
            param = param or detail.get("param")

    if code not in _UNSUPPORTED_CODES:
        return None
    return str(param) if param else None


async def chat_completion(client: Any, *, model: str, messages: list[Any], **params: Any):
    """`client.chat.completions.create`, minus whatever this model will not take."""
    refused = _REFUSED.setdefault(model, set())
    attempt = {k: v for k, v in params.items() if k not in refused}

    while True:
        try:
            return await client.chat.completions.create(
                model=model, messages=messages, **attempt
            )
        except BadRequestError as err:
            param = _refused_parameter(err)
            # Not about a parameter, or about one we are not sending: the
            # caller needs to see it.
            if not param or param not in attempt:
                raise
            refused.add(param)
            attempt.pop(param)
            log.info(
                "openai: %s does not accept %r — retrying without it", model, param
            )
