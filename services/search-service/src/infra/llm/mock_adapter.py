"""Mock LLM adapter for testing.

Returns a predictable response with <ANSWER> and <USED_REFS_JSON> blocks.
"""

from src.infra.llm.base import LLMAdapter, LLMAdapterError, LLMGenerationResult

MOCK_ANSWER = "Mock answer based on the provided context [1]."
MOCK_USED_REFS = [1]


class MockLLMAdapter(LLMAdapter):
    """Test-only LLM adapter that returns fixed responses.

    Can be configured to raise errors for failure scenario testing.
    """

    def __init__(
        self,
        *,
        answer: str = MOCK_ANSWER,
        used_refs: list[int] | None = None,
        error: LLMAdapterError | None = None,
    ) -> None:
        self._answer = answer
        self._used_refs = used_refs if used_refs is not None else MOCK_USED_REFS
        self._error = error

    async def generate(
        self, system_prompt: str, user_prompt: str, *, trace_id: str
    ) -> LLMGenerationResult:
        if self._error is not None:
            raise self._error

        refs_json = str(self._used_refs)
        text = (
            f"<ANSWER>\n{self._answer}\n</ANSWER>\n"
            f'<USED_REFS_JSON>\n{{"used_refs":{refs_json}}}\n</USED_REFS_JSON>'
        )
        return LLMGenerationResult(text=text)
