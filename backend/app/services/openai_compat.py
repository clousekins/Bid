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

import asyncio
import logging
from typing import Any

from openai import APIConnectionError, BadRequestError, InternalServerError

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
    message = ""

    body = getattr(err, "body", None)
    if isinstance(body, dict):
        detail = body.get("error") if isinstance(body.get("error"), dict) else body
        code = code or detail.get("code")
        param = param or detail.get("param")
        message = str(detail.get("message") or "")

    if code not in _UNSUPPORTED_CODES:
        # The Responses API sends the same refusal with `code: null`, and says
        # so only in the message: "Unsupported parameter: 'temperature' is not
        # supported with this model." That text is in the body; the exception's
        # own message is "Error code: 400 - {...}".
        if code is not None or not message.startswith(("Unsupported parameter", "Unsupported value")):
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


# How long a background response may run before it is given up on, and how
# often it is checked.
class BackgroundResponseFailed(RuntimeError):
    """The model run itself failed on OpenAI's side; worth another try."""


BACKGROUND_DEADLINE_S = 15 * 60
BACKGROUND_POLL_S = 4.0
_POLL_ERRORS_ALLOWED = 30


async def background_response_text(
    client: Any, *, model: str, instructions: str, input: str, **params: Any
) -> str:
    """One Responses API call run in background mode, polled until it is done.

    For networks that drop an idle connection. A reasoning model can think for
    a minute before it sends a byte, and a VPN tunnel that cuts connections
    silent for about ten seconds kills that request every time, streamed or
    not. In background mode the request returns at once with an id, and each
    poll after it is a short call of its own, so no connection is open long
    enough to be cut. A poll that fails is just tried again.

    Parameters the model refuses are dropped and remembered, as in
    chat_completion.
    """
    refused = _REFUSED.setdefault(model, set())
    attempt = {k: v for k, v in params.items() if k not in refused}
    while True:
        try:
            resp = await client.responses.create(
                model=model, instructions=instructions, input=input, background=True, **attempt
            )
            break
        except BadRequestError as err:
            param = _refused_parameter(err)
            if not param or param not in attempt:
                raise
            refused.add(param)
            attempt.pop(param)
            log.info("openai: %s does not accept %r — retrying without it", model, param)

    loop = asyncio.get_running_loop()
    deadline = loop.time() + BACKGROUND_DEADLINE_S
    poll_errors = 0
    try:
        while resp.status in ("queued", "in_progress"):
            if loop.time() > deadline:
                raise TimeoutError(f"OpenAI response {resp.id} still {resp.status} after {BACKGROUND_DEADLINE_S}s")
            await asyncio.sleep(BACKGROUND_POLL_S)
            try:
                resp = await client.responses.retrieve(resp.id)
            except (APIConnectionError, InternalServerError) as exc:
                poll_errors += 1
                if poll_errors > _POLL_ERRORS_ALLOWED:
                    raise
                log.warning("openai: polling %s failed (%s), trying again", resp.id, exc.__cause__ or exc)
    except BaseException:
        # Cancelled or given up: stop the model writing an answer nobody reads.
        if resp.status in ("queued", "in_progress"):
            try:
                await asyncio.shield(client.responses.cancel(resp.id))
            except BaseException:  # noqa: BLE001
                pass
        raise

    if resp.status == "completed":
        return (resp.output_text or "").strip()
    if resp.status == "incomplete":
        reason = getattr(resp.incomplete_details, "reason", None) or "unknown"
        raise RuntimeError(f"OpenAI response incomplete: {reason}")
    error = getattr(resp, "error", None)
    raise BackgroundResponseFailed(f"OpenAI response {resp.status}: {getattr(error, 'message', None) or resp.status}")
