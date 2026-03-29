"""Gemini LLM adapter for Search Service.

Uses google-genai SDK with Vertex AI backend.
Auth: Application Default Credentials (ADC), aligned with Pipeline Worker.
Retry policy: timeout/429/503 up to LLM_MAX_RETRIES with 200ms exponential backoff.
"""

import asyncio

from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

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
            try:
                response = await asyncio.wait_for(
                    self._client.aio.models.generate_content(
                        model=self._model_name,
                        contents=user_prompt,
                        config=genai_types.GenerateContentConfig(
                            system_instruction=system_prompt,
                            temperature=self._temperature,
                            max_output_tokens=self._max_output_tokens,
                        ),
                    ),
                    timeout=self._timeout_sec,
                )
                text = (response.text or "").strip()
                if not text:
                    raise LLMAdapterError(
                        code="INTERNAL_ERROR",
                        message="Gemini returned an empty response.",
                        retryable=False,
                    )
                return LLMGenerationResult(text=text)

            except asyncio.TimeoutError:
                raise RetryableError(LLMAdapterError(
                    code="TIMEOUT",
                    message=f"Gemini timed out after {self._timeout_sec}s",
                    retryable=True,
                ))
            except genai_errors.APIError as exc:
                if exc.code in (429, 503):
                    raise RetryableError(LLMAdapterError(
                        code="RATE_LIMITED" if exc.code == 429 else "UNAVAILABLE",
                        message=f"Gemini {'rate limited (429)' if exc.code == 429 else 'service unavailable (503)'}",
                        retryable=True,
                    ))
                if exc.code == 403:
                    raise LLMAdapterError(
                        code="AUTH_ERROR",
                        message="Gemini authentication failed",
                        retryable=False,
                    )
                raise LLMAdapterError(
                    code="INTERNAL_ERROR",
                    message=f"Gemini API error: {exc}",
                    retryable=False,
                )
            except (LLMAdapterError, RetryableError):
                raise
            except Exception as exc:
                raise LLMAdapterError(
                    code="INTERNAL_ERROR",
                    message=f"Unexpected Gemini error: {exc}",
                    retryable=False,
                ) from exc

        return await retry_with_backoff(
            _attempt, max_retries=self._max_retries
        )

    async def aclose(self) -> None:
        await self._client.aio.aclose()
        self._client.close()
