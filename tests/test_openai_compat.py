"""Parameters a model will not take.

Newer models refuse settings their predecessors accepted — gpt-5.6-luna
answers any `temperature` but its default with a 400 — and four call sites
here ask for 0, 0.1, 0.2 and 0.4. Changing the configured model broke every
one of them, so the helper exists to drop what the API names and try again.

These tests use a stand-in client. The shape of the exception is the thing
worth pinning: the SDK puts `code` and `param` on the error object, and `body`
holds the error itself rather than the {"error": …} envelope sent over the
wire — reading `body["error"]` finds nothing, and the retry never happens.
"""
from __future__ import annotations

import httpx
import pytest
from openai import BadRequestError

from app.services import openai_compat
from app.services.openai_compat import chat_completion


def _bad_request(param: str, code: str = "unsupported_value") -> BadRequestError:
    """The 400 the API actually sends for an unusable parameter."""
    body = {
        "message": f"Unsupported value: '{param}' does not support 0.2 with this model.",
        "type": "invalid_request_error",
        "param": param,
        "code": code,
    }
    return BadRequestError(
        body["message"],
        response=httpx.Response(400, request=httpx.Request("POST", "https://api.openai.com")),
        body=body,
    )


class _Client:
    """Records each attempt, and refuses the named parameters."""

    def __init__(self, refuses: set[str] | None = None, error: Exception | None = None):
        self.refuses = refuses or set()
        self.error = error
        self.calls: list[dict] = []
        self.chat = type("chat", (), {"completions": self})()

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        for param in self.refuses:
            if param in kwargs:
                raise _bad_request(param)
        return {"ok": True, "used": kwargs}


@pytest.fixture(autouse=True)
def _forget_what_was_learned():
    openai_compat._REFUSED.clear()
    yield
    openai_compat._REFUSED.clear()


class TestDroppingWhatIsRefused:
    async def test_an_accepted_parameter_is_left_alone(self):
        client = _Client()
        result = await chat_completion(client, model="m", messages=[], temperature=0.2)
        assert result["used"]["temperature"] == 0.2
        assert len(client.calls) == 1, "no retry when nothing was refused"

    async def test_a_refused_parameter_is_dropped_and_the_call_retried(self):
        client = _Client(refuses={"temperature"})
        result = await chat_completion(client, model="m", messages=[], temperature=0.2)
        assert "temperature" not in result["used"]
        assert len(client.calls) == 2
        assert client.calls[0]["temperature"] == 0.2, "it tries the value first"

    def test_the_error_shape_is_the_one_the_sdk_produces(self):
        """The bug this guards: body is the error, not {"error": …}."""
        err = _bad_request("temperature")
        assert err.param == "temperature"
        assert err.code == "unsupported_value"
        assert "error" not in (err.body or {}), "body is the error object itself"
        assert openai_compat._refused_parameter(err) == "temperature"

    async def test_everything_else_survives_the_retry(self):
        client = _Client(refuses={"temperature"})
        result = await chat_completion(
            client, model="m", messages=[{"role": "user", "content": "hi"}],
            temperature=0.2, top_p=0.9,
        )
        assert result["used"]["top_p"] == 0.9
        assert result["used"]["model"] == "m"
        assert result["used"]["messages"] == [{"role": "user", "content": "hi"}]

    async def test_more_than_one_refusal_is_worked_through(self):
        client = _Client(refuses={"temperature", "top_p"})
        result = await chat_completion(
            client, model="m", messages=[], temperature=0.2, top_p=0.9
        )
        assert result["used"] == {"model": "m", "messages": []}
        assert len(client.calls) == 3


class TestItOnlyPaysOnce:
    async def test_a_later_call_does_not_repeat_the_refused_attempt(self):
        client = _Client(refuses={"temperature"})
        await chat_completion(client, model="m", messages=[], temperature=0.2)
        assert len(client.calls) == 2

        await chat_completion(client, model="m", messages=[], temperature=0.4)
        assert len(client.calls) == 3, "the second call goes straight through"
        assert "temperature" not in client.calls[-1]

    async def test_what_one_model_refuses_is_not_assumed_of_another(self):
        client = _Client(refuses={"temperature"})
        await chat_completion(client, model="strict", messages=[], temperature=0.2)
        client.refuses = set()
        result = await chat_completion(client, model="lenient", messages=[], temperature=0.2)
        assert result["used"]["temperature"] == 0.2


class TestRealFailuresStillSurface:
    async def test_an_unknown_model_is_not_swallowed(self):
        error = BadRequestError(
            "The model does not exist",
            response=httpx.Response(400, request=httpx.Request("POST", "https://x")),
            body={"message": "no such model", "code": "model_not_found", "param": None},
        )
        client = _Client(error=error)
        with pytest.raises(BadRequestError):
            await chat_completion(client, model="m", messages=[], temperature=0.2)

    async def test_a_complaint_about_something_we_did_not_send_is_not_swallowed(self):
        """Dropping a parameter we never sent would loop forever."""
        client = _Client(error=_bad_request("audio"))
        with pytest.raises(BadRequestError):
            await chat_completion(client, model="m", messages=[], temperature=0.2)
        assert len(client.calls) == 1

    async def test_other_errors_pass_through_untouched(self):
        client = _Client(error=RuntimeError("connection reset"))
        with pytest.raises(RuntimeError):
            await chat_completion(client, model="m", messages=[], temperature=0.2)


def test_every_chat_call_site_goes_through_the_helper():
    """A call site added later must not reintroduce the failure."""
    from pathlib import Path

    services = Path(__file__).resolve().parents[1] / "backend" / "app" / "services"
    offenders = [
        path.name
        for path in services.glob("*.py")
        if path.name != "openai_compat.py"
        and "chat.completions.create" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, f"these call the API directly: {offenders}"
