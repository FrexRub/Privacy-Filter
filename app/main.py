import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field


API_TOKEN = os.getenv("PRIVACY_FILTER_API_TOKEN")
DEFAULT_DEVICE = os.getenv("OPF_DEVICE", "cpu")
DEFAULT_OUTPUT_MODE = os.getenv("OPF_OUTPUT_MODE", "typed")
OPF_TIMEOUT_SECONDS = int(os.getenv("OPF_TIMEOUT_SECONDS", "300"))
ALLOWED_DEVICES = {"cpu", "cuda"}
ALLOWED_OUTPUT_MODES = {"typed", "redacted"}


@dataclass(frozen=True)
class Rule:
    entity_type: str
    pattern: re.Pattern[str]
    replacement: str


class RedactRequest(BaseModel):
    text: str = Field(default="")
    device: str = Field(default=DEFAULT_DEVICE)
    output_mode: str = Field(default=DEFAULT_OUTPUT_MODE)
    apply_ru_rules: bool = Field(default=True)


class RuleSpan(BaseModel):
    label: str
    start: int
    end: int
    placeholder: str


app = FastAPI(title="OpenAI Privacy Filter API", version="0.2.0")


RU_RULES = [
    Rule(
        "private_email",
        re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
        "[PRIVATE_EMAIL]",
    ),
    Rule(
        "private_date",
        re.compile(
            r"\b(?:дата\s+рождения|д\.?\s*р\.?|родил(?:ся|ась)?):?\s*"
            r"(?:[0-3]?\d[./-][01]?\d[./-](?:19|20)\d{2}|[0-3]?\d\s+"
            r"(?:января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+"
            r"(?:19|20)\d{2})",
            re.IGNORECASE,
        ),
        "[PRIVATE_DATE]",
    ),
    Rule(
        "account_number",
        re.compile(
            r"\b(?:паспорт(?:\s+рф)?|серия\s+и\s+номер):?\s*"
            r"(?:\d{2}\s?\d{2}\s?\d{6}|\d{4}\s?\d{6})\b",
            re.IGNORECASE,
        ),
        "[ACCOUNT_NUMBER]",
    ),
    Rule(
        "account_number",
        re.compile(r"\b(?:снилс:?\s*)?\d{3}[- ]?\d{3}[- ]?\d{3}[- ]?\d{2}\b", re.IGNORECASE),
        "[ACCOUNT_NUMBER]",
    ),
    Rule(
        "account_number",
        re.compile(r"\b(?:инн:?\s*)?\d{10}(?:\d{2})?\b", re.IGNORECASE),
        "[ACCOUNT_NUMBER]",
    ),
    Rule(
        "private_phone",
        re.compile(
            r"(?<!\d)(?:\+7|8)?[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}(?!\d)"
        ),
        "[PRIVATE_PHONE]",
    ),
    Rule(
        "private_address",
        re.compile(
            r"\b(?:адрес|прожива(?:ю|ет)|регистрация):?\s*"
            r"[^.\n;]*(?:ул\.?|улица|проспект|пр-т|пер\.?|переулок|шоссе|дом|д\.|кв\.|квартира)"
            r"[^.\n;]*",
            re.IGNORECASE,
        ),
        "[PRIVATE_ADDRESS]",
    ),
    Rule(
        "private_address",
        re.compile(
            r"\b(?:г\.|город)\s*[А-ЯЁA-Z][А-ЯЁа-яёA-Za-z -]+,\s*"
            r"(?:ул\.?|улица|проспект|пр-т|пер\.?|переулок|шоссе)\s*[^.\n;]+",
            re.IGNORECASE,
        ),
        "[PRIVATE_ADDRESS]",
    ),
    Rule(
        "private_person",
        re.compile(
            r"\b(?:фио|ф\.и\.о\.|кандидат|соискатель):?\s*"
            r"[А-ЯЁ][а-яё-]+\s+[А-ЯЁ][а-яё-]+(?:\s+[А-ЯЁ][а-яё-]+)?\b",
            re.IGNORECASE,
        ),
        "[PRIVATE_PERSON]",
    ),
    Rule(
        "private_person",
        re.compile(
            r"\b[А-ЯЁ][а-яё-]+\s+[А-ЯЁ][а-яё-]+\s+"
            r"(?:[А-ЯЁ][а-яё-]+(?:вич|вна|ич|на))\b"
        ),
        "[PRIVATE_PERSON]",
    ),
]


def _require_token(authorization: str | None) -> None:
    if not API_TOKEN:
        return

    if authorization != f"Bearer {API_TOKEN}":
        raise HTTPException(status_code=401, detail="Unauthorized")


def _validate_request(payload: RedactRequest) -> None:
    if payload.device not in ALLOWED_DEVICES:
        raise HTTPException(
            status_code=422,
            detail=f"device must be one of: {', '.join(sorted(ALLOWED_DEVICES))}",
        )

    if payload.output_mode not in ALLOWED_OUTPUT_MODES:
        raise HTTPException(
            status_code=422,
            detail=f"output_mode must be one of: {', '.join(sorted(ALLOWED_OUTPUT_MODES))}",
        )


def _empty_response(text: str, output_mode: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "summary": {
            "output_mode": output_mode,
            "span_count": 0,
            "by_label": {},
        },
        "text": text,
        "detected_spans": [],
        "redacted_text": text,
    }


def _run_opf(payload: RedactRequest) -> dict[str, Any]:
    with tempfile.NamedTemporaryFile("w+", encoding="utf-8", delete=False) as temp_file:
        temp_file.write(payload.text)
        input_path = temp_file.name

    try:
        cmd = [
            "opf",
            "--device",
            payload.device,
            "--output-mode",
            payload.output_mode,
            "--format",
            "json",
            "--no-print-color-coded-text",
            "-f",
            input_path,
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=OPF_TIMEOUT_SECONDS,
            check=False,
        )

        if result.returncode != 0:
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "opf failed",
                    "stderr": result.stderr,
                    "stdout": result.stdout,
                },
            )

        stdout = result.stdout.strip()
        if not stdout:
            return _empty_response(payload.text, payload.output_mode)

        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            lines = [line for line in stdout.splitlines() if line.strip()]
            for line in reversed(lines):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue

            raise HTTPException(
                status_code=500,
                detail={
                    "error": "opf returned non-json output",
                    "stdout": stdout,
                    "stderr": result.stderr,
                },
            )
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(
            status_code=504,
            detail={
                "error": "opf timed out",
                "timeout_seconds": OPF_TIMEOUT_SECONDS,
                "stderr": exc.stderr,
                "stdout": exc.stdout,
            },
        ) from exc
    finally:
        try:
            os.remove(input_path)
        except OSError:
            pass


def _collect_ru_spans(text: str, existing_spans: list[dict[str, Any]]) -> list[RuleSpan]:
    occupied = [(span["start"], span["end"]) for span in existing_spans]
    spans: list[RuleSpan] = []

    for rule in RU_RULES:
        for match in rule.pattern.finditer(text):
            start, end = match.span()
            if any(start < used_end and end > used_start for used_start, used_end in occupied):
                continue

            spans.append(
                RuleSpan(
                    label=rule.entity_type,
                    start=start,
                    end=end,
                    placeholder=rule.replacement,
                )
            )
            occupied.append((start, end))

    return sorted(spans, key=lambda span: span.start)


def _placeholder_for_span(span: dict[str, Any], output_mode: str) -> str:
    placeholder = span.get("placeholder")
    if isinstance(placeholder, str) and placeholder:
        return placeholder

    if output_mode == "redacted":
        return "[REDACTED]"

    label = str(span.get("label", "redacted"))
    return f"[{label.upper()}]"


def _build_redacted_text(text: str, spans: list[dict[str, Any]], output_mode: str) -> str:
    redacted_text = text
    for span in sorted(spans, key=lambda item: item["start"], reverse=True):
        redacted_text = (
            redacted_text[: span["start"]]
            + _placeholder_for_span(span, output_mode)
            + redacted_text[span["end"] :]
        )
    return redacted_text


def _apply_ru_rules(opf_response: dict[str, Any], output_mode: str) -> dict[str, Any]:
    text = opf_response.get("text", "")
    existing_spans = list(opf_response.get("detected_spans", []))
    extra_spans = _collect_ru_spans(text, existing_spans)

    if not extra_spans:
        return opf_response

    detected_spans = [
        *existing_spans,
        *[
            {
                "label": span.label,
                "start": span.start,
                "end": span.end,
                "text": text[span.start : span.end],
                "placeholder": span.placeholder,
                "source": "ru_rules",
            }
            for span in extra_spans
        ],
    ]
    detected_spans.sort(key=lambda span: span["start"])

    by_label: dict[str, int] = {}
    for span in detected_spans:
        label = span["label"]
        by_label[label] = by_label.get(label, 0) + 1

    opf_response["detected_spans"] = detected_spans
    opf_response["redacted_text"] = _build_redacted_text(text, detected_spans, output_mode)
    opf_response["summary"] = {
        **opf_response.get("summary", {}),
        "span_count": len(detected_spans),
        "by_label": by_label,
    }
    return opf_response


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/redact")
def redact(
    payload: RedactRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_token(authorization)
    _validate_request(payload)

    if not payload.text.strip():
        return _empty_response(payload.text, payload.output_mode)

    response = _run_opf(payload)
    if payload.apply_ru_rules:
        response = _apply_ru_rules(response, payload.output_mode)

    return response


@app.post("/mask")
def mask(
    payload: RedactRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    return redact(payload, authorization)
