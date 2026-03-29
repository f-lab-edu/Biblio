"""LLMAdapter abstract interface for Search Service.

Concrete implementations: GeminiLLMAdapter, ClaudeLLMAdapter, MockLLMAdapter.
Wired via bootstrap based on LLM_PROVIDER setting.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(slots=True)
class LLMGenerationResult:
    """Raw LLM output containing <ANSWER> and <USED_REFS_JSON> blocks."""

    text: str


class LLMAdapterError(Exception):
    """Error from LLM adapter with retryability info."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        retryable: bool,
    ) -> None:
        super().__init__(message)
        self.code = code  # TIMEOUT, RATE_LIMITED, UNAVAILABLE, AUTH_ERROR, INTERNAL_ERROR
        self.message = message
        self.retryable = retryable


class LLMAdapter(ABC):
    @abstractmethod
    async def generate(
        self, system_prompt: str, user_prompt: str, *, trace_id: str
    ) -> LLMGenerationResult:
        """Generate LLM response for the given prompts.

        *system_prompt* carries invariant instructions (grounding rules,
        output format).  *user_prompt* carries the request-specific query
        and context blocks.

        Returns LLMGenerationResult with raw text containing
        exactly one <ANSWER>...</ANSWER> block and one
        <USED_REFS_JSON>...</USED_REFS_JSON> block.

        Raises LLMAdapterError on failure.
        """
