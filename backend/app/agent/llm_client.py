import asyncio
import time
from functools import lru_cache

from openai import AsyncOpenAI

from app.config import settings


_llm_semaphore = asyncio.Semaphore(max(1, settings.llm_max_concurrent))
_circuit_lock = asyncio.Lock()
_consecutive_failures = 0
_circuit_opened_at: float | None = None


async def _check_circuit() -> None:
    global _consecutive_failures, _circuit_opened_at
    async with _circuit_lock:
        if _circuit_opened_at is None:
            return
        elapsed = time.monotonic() - _circuit_opened_at
        if elapsed < settings.llm_circuit_breaker_cooldown_seconds:
            raise RuntimeError("LLM circuit breaker is open; retry after cooldown")
        # Allow a trial request after cooldown. If it fails, the breaker opens again.
        _circuit_opened_at = None
        _consecutive_failures = 0


async def _record_llm_success() -> None:
    global _consecutive_failures, _circuit_opened_at
    async with _circuit_lock:
        _consecutive_failures = 0
        _circuit_opened_at = None


async def _record_llm_failure() -> None:
    global _consecutive_failures, _circuit_opened_at
    async with _circuit_lock:
        _consecutive_failures += 1
        if _consecutive_failures >= settings.llm_circuit_breaker_failures:
            _circuit_opened_at = time.monotonic()


@lru_cache(maxsize=1)
def get_llm_client() -> AsyncOpenAI:
    key = settings.deepseek_api_key.get_secret_value()
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY is empty; set it in the project-root .env")
    return AsyncOpenAI(
        api_key=key,
        base_url=settings.llm_base_url,
        timeout=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
    )


async def generate(messages: list[dict], max_tokens: int = 900) -> str:
    await _check_circuit()
    try:
        async with _llm_semaphore:
            response = await get_llm_client().chat.completions.create(
                model=settings.llm_model,
                messages=messages,
                temperature=0.1,
                max_tokens=max_tokens,
                extra_body={"thinking": {"type": "disabled"}},
            )
        content = response.choices[0].message.content
        if not content or not content.strip():
            raise RuntimeError("DeepSeek returned an empty response")
        await _record_llm_success()
        return content.strip()
    except Exception:
        await _record_llm_failure()
        raise
