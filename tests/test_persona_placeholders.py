from __future__ import annotations

from app.services.persona import fill_identity_placeholders

PROMPT = (
    "Arlington, Virginia | me@example.com | {{phone}} | https://linkedin.com/in/me/\n"
    '<div class="contact">Arlington, Virginia &nbsp;&nbsp;&nbsp;&nbsp; me@example.com '
    "&nbsp;&nbsp;&nbsp;&nbsp; {{phone}} &nbsp;&nbsp;&nbsp;&nbsp; <a>linkedin</a></div>"
)


def test_a_recorded_phone_fills_every_slot():
    out = fill_identity_placeholders(PROMPT, {"phone": "(703) 555-0100"})
    assert "{{phone}}" not in out
    assert out.count("(703) 555-0100") == 2


def test_no_phone_leaves_no_empty_slot_or_doubled_separator():
    out = fill_identity_placeholders(PROMPT, {"phone": ""})
    assert "{{phone}}" not in out
    assert "| |" not in out and "|  |" not in out
    assert out.splitlines()[0] == "Arlington, Virginia | me@example.com | https://linkedin.com/in/me/"
    assert (
        "me@example.com &nbsp;&nbsp;&nbsp;&nbsp; <a>linkedin</a>" in out.splitlines()[1]
    )


def test_prompts_without_a_slot_are_untouched():
    text = "Columbia, Maryland | a@b.com | (301) 857-9736"
    assert fill_identity_placeholders(text, {"phone": ""}) == text
