"""Gemini LLM adapter for Search Service.

Uses google-genai SDK with Vertex AI backend.
Auth: Application Default Credentials (ADC), aligned with Pipeline Worker.
Retry policy: timeout/429/503 up to LLM_MAX_RETRIES with 200ms exponential backoff.
"""

import asyncio

from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from src.common.logging import warning as log_warning
from src.common.retry import RetryableError, retry_with_backoff
from src.infra.llm.base import LLMAdapter, LLMAdapterError, LLMGenerationResult


class GeminiLLMAdapter(LLMAdapter):
    def __init__(
        self,
        *,
        project_id: str,
        location: str,
        model_name: str,
        timeout_sec: int = 3,
        max_retries: int = 1,
        temperature: float = 0.2,
        max_output_tokens: int = 512,
    ) -> None:
        self._client = genai.Client(
            vertexai=True,
            project=project_id,
            location=location,
        )
        self._model_name = model_name
        self._timeout_sec = timeout_sec
        self._max_retries = max_retries
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens

    async def generate(
        self, system_prompt: str, user_prompt: str, *, trace_id: str
    ) -> LLMGenerationResult:
        async def _attempt() -> LLMGenerationResult:
            return await self._generate_once(system_prompt, user_prompt, trace_id)

        return await retry_with_backoff(_attempt, max_retries=self._max_retries)

    async def _generate_once(
        self, system_prompt: str, user_prompt: str, trace_id: str
    ) -> LLMGenerationResult:
        try:
            response = await asyncio.wait_for(
                self._call_gemini(system_prompt, user_prompt),
                timeout=self._timeout_sec,
            )
            text = self._extract_text(response, trace_id)
            self._log_non_stop_finish_reason(response, trace_id, text)
            return LLMGenerationResult(text=text)
        except (LLMAdapterError, RetryableError):
            raise
        except Exception as exc:
            raise self._translate_error(exc) from exc

    async def _call_gemini(self, system_prompt: str, user_prompt: str):
        return await self._client.aio.models.generate_content(
            model=self._model_name,
            contents=user_prompt,
            config=self._build_generation_config(system_prompt),
        )

    def _build_generation_config(
        self, system_prompt: str
    ) -> genai_types.GenerateContentConfig:
        return genai_types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=self._temperature,
            max_output_tokens=self._max_output_tokens,
        )

    def _extract_text(self, response, trace_id: str) -> str:
        text = (response.text or "").strip()
        if not text:
            self._log_empty_response(response, trace_id)
            raise LLMAdapterError(
                code="INTERNAL_ERROR",
                message="Gemini returned an empty response.",
                retryable=False,
            )
        return text

    def _log_empty_response(self, response, trace_id: str) -> None:
        log_warning(
            "gemini.empty_response",
            trace_id=trace_id,
            model_name=self._model_name,
            candidate_count=self._candidate_count(response),
            finish_reason=self._first_finish_reason(response),
            block_reason=self._block_reason(response),
        )

    def _log_non_stop_finish_reason(
        self, response, trace_id: str, text: str
    ) -> None:
        finish_reason = self._first_finish_reason(response)
        if finish_reason in (None, "STOP"):
            return
        log_warning(
            "gemini.non_stop_finish_reason",
            trace_id=trace_id,
            model_name=self._model_name,
            candidate_count=self._candidate_count(response),
            finish_reason=finish_reason,
            block_reason=self._block_reason(response),
            response_chars=len(text),
        )

    @staticmethod
    def _candidate_count(response) -> int:
        candidates = getattr(response, "candidates", None) or []
        return len(candidates)

    @staticmethod
    def _first_finish_reason(response) -> str | None:
        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return None
        return getattr(candidates[0], "finish_reason", None)

    @staticmethod
    def _block_reason(response) -> str | None:
        prompt_feedback = getattr(response, "prompt_feedback", None)
        if prompt_feedback is None:
            return None
        return getattr(prompt_feedback, "block_reason", None)

    def _translate_error(self, exc: Exception) -> Exception:
        if isinstance(exc, asyncio.TimeoutError):
            return RetryableError(
                LLMAdapterError(
                    code="TIMEOUT",
                    message=f"Gemini timed out after {self._timeout_sec}s",
                    retryable=True,
                )
            )
        if isinstance(exc, genai_errors.APIError):
            return self._translate_api_error(exc)
        return LLMAdapterError(
            code="INTERNAL_ERROR",
            message=f"Unexpected Gemini error: {exc}",
            retryable=False,
        )

    def _translate_api_error(self, exc: genai_errors.APIError) -> Exception:
        if exc.code == 429:
            return RetryableError(
                LLMAdapterError(
                    code="RATE_LIMITED",
                    message="Gemini rate limited (429)",
                    retryable=True,
                )
            )
        if exc.code == 503:
            return RetryableError(
                LLMAdapterError(
                    code="UNAVAILABLE",
                    message="Gemini service unavailable (503)",
                    retryable=True,
                )
            )
        if exc.code == 403:
            return LLMAdapterError(
                code="AUTH_ERROR",
                message="Gemini authentication failed",
                retryable=False,
            )
        return LLMAdapterError(
            code="INTERNAL_ERROR",
            message=f"Gemini API error: {exc}",
            retryable=False,
        )

    async def aclose(self) -> None:
        await self._client.aio.aclose()
        self._client.close()
