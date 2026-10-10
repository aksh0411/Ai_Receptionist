"""Dialogue policy: behavior text as data, loaded and validated exactly once.

Python keeps execution, validation, retries, and safety enforcement; this module
only supplies the objectives/scripts/bans the system prompt is built from. The
policy is read once, validated loudly (a malformed file kills startup with the
exact offending key — never a silently broken agent), cached, and rendered to
prose for the prompt. Editing how the agent talks = editing the JSON, no code.
"""

import json
from pathlib import Path

POLICY_FILE = Path(__file__).parent / "dialogue_policy.json"

_POLICY: dict | None = None
_RENDERED: str | None = None


def _validate(policy: dict) -> None:
    """Fail fast on structure problems, naming the exact offending key."""

    def need(cond: bool, msg: str) -> None:
        if not cond:
            raise RuntimeError(f"dialogue_policy.json invalid: {msg}")

    need(isinstance(policy, dict), "top level must be an object")
    need(isinstance(policy.get("version"), int), "'version' must be an integer")
    need(isinstance(policy.get("style"), dict), "'style' must be an object")
    need(isinstance(policy.get("intents"), dict) and policy["intents"], "'intents' must be a non-empty object")

    def need_str_list(obj: dict, key: str, path: str) -> None:
        if key in obj:
            need(isinstance(obj[key], list) and all(isinstance(x, str) and x.strip() for x in obj[key]),
                 f"'{path}.{key}' must be a list of non-empty strings")

    need_str_list(policy["style"], "rules", "style")
    need_str_list(policy["style"], "never", "style")
    for name, intent in policy["intents"].items():
        need(isinstance(intent, dict), f"intents.{name} must be an object")
        need(isinstance(intent.get("objective", ""), str) and intent.get("objective", "").strip(),
             f"intents.{name}.objective must be a non-empty string")
        need_str_list(intent, "never", f"intents.{name}")
        asks = intent.get("asks")
        if asks is not None:
            need(isinstance(asks, dict), f"intents.{name}.asks must be an object")
            for label, variants in asks.items():
                need(isinstance(variants, dict) and variants,
                     f"intents.{name}.asks.{label} must be an object of language -> text")
                for lang, text in variants.items():
                    need(isinstance(text, str) and text.strip(),
                         f"intents.{name}.asks.{label}.{lang} must be a non-empty string")


def _render(policy: dict) -> str:
    """Prose for the system prompt — the model never sees raw JSON."""
    lines: list[str] = []
    style = policy.get("style", {})
    for rule in style.get("rules", []):
        lines.append(f"Always: {rule}")
    for rule in style.get("never", []):
        lines.append(f"Never: {rule}")
    for name, intent in policy["intents"].items():
        lines.append(f"{name.upper()} — {intent['objective']}")
        for label, variants in intent.get("asks", {}).items():
            primary = variants.get("en") or next(iter(variants.values()))
            extra = " / ".join(t for k, t in variants.items() if k != "en" and t != primary)
            lines.append(f"  ask ({label}): {primary}" + (f"  [{extra}]" if extra else ""))
        for rule in intent.get("rules", []):
            lines.append(f"  - {rule}")
        for rule in intent.get("never", []):
            lines.append(f"  never: {rule}")
    return "\n".join(lines)


def load() -> dict:
    global _POLICY, _RENDERED
    if _POLICY is None:
        _POLICY = json.loads(POLICY_FILE.read_text(encoding="utf-8"))
        _validate(_POLICY)
        _RENDERED = _render(_POLICY)
    return _POLICY


def version() -> int:
    return load()["version"]


def rendered() -> str:
    load()
    return _RENDERED
