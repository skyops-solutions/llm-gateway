"""Mask emails and phone numbers before a prompt leaves the gateway."""

from __future__ import annotations

import re

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# International or local numbers with at least 9 digits, allowing spaces, dots, dashes and brackets.
PHONE = re.compile(r"(?<![\w])\+?\d[\d\s().-]{7,}\d(?![\w])")


def _digits(s: str) -> int:
    return sum(c.isdigit() for c in s)


def mask_text(text: str) -> tuple[str, dict[str, int]]:
    counts = {"email": 0, "phone": 0}

    def _email(_: re.Match) -> str:
        counts["email"] += 1
        return "[EMAIL]"

    def _phone(m: re.Match) -> str:
        if _digits(m.group(0)) < 9:
            return m.group(0)
        counts["phone"] += 1
        return "[PHONE]"

    text = EMAIL.sub(_email, text)
    text = PHONE.sub(_phone, text)
    return text, counts


def mask_messages(messages: list[dict]) -> tuple[list[dict], dict[str, int]]:
    total = {"email": 0, "phone": 0}
    out = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            masked, counts = mask_text(content)
            for k, v in counts.items():
                total[k] += v
            msg = {**msg, "content": masked}
        out.append(msg)
    return out, total
