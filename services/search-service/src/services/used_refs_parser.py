"""Parsing helpers for Search Service LLM output blocks."""

import json
import re

_ANSWER_BLOCK_RE = re.compile(r"<ANSWER>(.*?)</ANSWER>", re.DOTALL)
_USED_REFS_BLOCK_RE = re.compile(
    r"<USED_REFS_JSON>(.*?)</USED_REFS_JSON>",
    re.DOTALL,
)


def extract_answer(llm_text: str) -> str:
    """Extract the final answer body from the required <ANSWER> block."""
    matches = _ANSWER_BLOCK_RE.findall(llm_text)
    if len(matches) != 1:
        raise ValueError("LLM response must contain exactly one <ANSWER> block.")

    answer = matches[0].strip()
    if not answer:
        raise ValueError("LLM response <ANSWER> block must not be blank.")

    return answer


def parse_used_refs(llm_text: str, max_ref: int) -> list[int]:
    """Parse and sanitize used_refs from the metadata block only."""
    matches = _USED_REFS_BLOCK_RE.findall(llm_text)
    if len(matches) != 1:
        return []

    try:
        payload = json.loads(matches[0].strip())
    except json.JSONDecodeError:
        return []

    raw_used_refs = payload.get("used_refs")
    if not isinstance(raw_used_refs, list):
        return []

    used_refs: list[int] = []
    seen: set[int] = set()
    for item in raw_used_refs:
        if isinstance(item, bool) or not isinstance(item, int):
            continue
        if item < 1 or item > max_ref or item in seen:
            continue
        seen.add(item)
        used_refs.append(item)

    return used_refs
