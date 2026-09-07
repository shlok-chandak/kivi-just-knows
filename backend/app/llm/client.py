"""The only module that talks to the model provider.

Everything else asks for a schema-shaped result and gets usage numbers back,
so swapping providers means rewriting this file and nothing else.
"""

import logging
import time
from dataclasses import dataclass
from typing import Any, TypeVar

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel, ValidationError

from app.config import settings
from app.llm.pricing import cost_usd, rate_for

logger = logging.getLogger("kivi.llm")

T = TypeVar("T", bound=BaseModel)

SMALL = "small"
LARGE = "large"


class LLMError(RuntimeError):
    """Call failed. Raised so the queue records it and retries with backoff."""


class LLMRateLimited(LLMError):
    """Provider returned 429. Same handling, but worth distinguishing."""


@dataclass(frozen=True)
class Usage:
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost_usd: float


@dataclass(frozen=True)
class Completion:
    """A validated result plus what it cost to get it."""

    value: Any
    usage: Usage


class LLMClient:
    def __init__(self, api_key: str | None = None) -> None:
        key = api_key or settings.llm_api_key
        if not key:
            raise LLMError("LLM_API_KEY is not set")
        self._client = genai.Client(api_key=key)

    def structured(
        self,
        *,
        prompt: str,
        schema: type[T],
        system: str | None = None,
        tier: str = SMALL,
        temperature: float = 0.0,
    ) -> Completion:
        """Ask for a result conforming to `schema`.

        The provider constrains generation to the schema, so the reply parses
        as that type or the call is treated as failed. Temperature defaults to
        0: extraction should be reproducible, not creative.
        """
        model = (
            settings.llm_large_model if tier == LARGE else settings.llm_small_model
        )

        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema,
            temperature=temperature,
            system_instruction=system,
            http_options=types.HttpOptions(
                timeout=int(settings.llm_timeout_seconds * 1000)
            ),
        )

        started = time.perf_counter()
        try:
            response = self._client.models.generate_content(
                model=model, contents=prompt, config=config
            )
        except genai_errors.ClientError as exc:
            if getattr(exc, "code", None) == 429:
                raise LLMRateLimited(str(exc)) from exc
            raise LLMError(f"{model}: {exc}") from exc
        except genai_errors.ServerError as exc:
            raise LLMError(f"{model}: {exc}") from exc

        latency_ms = int((time.perf_counter() - started) * 1000)
        usage = self._usage(model, response, latency_ms)

        parsed = getattr(response, "parsed", None)
        if parsed is None:
            # Constrained decoding should make this unreachable; treat it as a
            # failure rather than guessing at malformed output.
            raise LLMError(f"{model}: response did not conform to {schema.__name__}")

        if not isinstance(parsed, schema):
            try:
                parsed = schema.model_validate(parsed)
            except ValidationError as exc:
                raise LLMError(f"{model}: {exc}") from exc

        return Completion(value=parsed, usage=usage)

    @staticmethod
    def _usage(model: str, response: Any, latency_ms: int) -> Usage:
        meta = getattr(response, "usage_metadata", None)

        input_tokens = getattr(meta, "prompt_token_count", None) or 0
        output_tokens = getattr(meta, "candidates_token_count", None) or 0

        # Reasoning tokens are billed as output; excluding them would
        # understate the cost of a thinking model.
        output_tokens += getattr(meta, "thoughts_token_count", None) or 0

        if rate_for(model)[1]:
            logger.warning(
                "no published rate for %s; cost reported at the highest "
                "known rate and is an upper bound, not a measurement",
                model,
            )

        return Usage(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            cost_usd=cost_usd(model, input_tokens, output_tokens),
        )


_client: LLMClient | None = None


def get_client() -> LLMClient:
    """Shared client. Constructed on first use so imports stay key-free."""
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
