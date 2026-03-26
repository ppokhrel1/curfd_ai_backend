import logging
import os
from functools import lru_cache

from langchain_core.language_models import BaseChatModel

from app.core.config import settings

logger = logging.getLogger(__name__)

_DEFAULTS = {
    "gemini": "gemini-2.5-flash",
    "anthropic": "claude-sonnet-4-6",
    "groq": "llama-3.3-70b-versatile",
    "openai": "gpt-4o",
}

# Providers that support extended thinking
_THINKING_PROVIDERS = frozenset({"anthropic"})

# Models that support adaptive thinking (others use enabled + budget_tokens)
_ADAPTIVE_THINKING_MODELS = frozenset({"claude-opus-4-6"})


@lru_cache(maxsize=16)
def get_llm(
    provider: str | None = None,
    model: str | None = None,
    thinking: bool = False,
    temperature_override: float | None = None,
    max_tokens_override: int | None = None,
) -> BaseChatModel:
    """Return a LangChain chat model, cached by (provider, model, thinking, overrides).

    Args:
        temperature_override: If set, use this temperature instead of settings.llm_temperature.
        max_tokens_override: If set, use this max_tokens instead of the provider default.
    """
    provider = (provider or settings.llm_provider).lower()
    model = model or settings.llm_model or _DEFAULTS.get(provider)
    temperature = temperature_override if temperature_override is not None else settings.llm_temperature
    max_tokens = max_tokens_override or 16_000

    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=model,
            google_api_key=settings.gemini_api_key or os.getenv("GEMINI_API_KEY"),
            temperature=temperature,
        )
    elif provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        kwargs: dict = {
            "model": model,
            "api_key": settings.anthropic_api_key or os.getenv("ANTHROPIC_API_KEY"),
        }
        if thinking:
            if model in _ADAPTIVE_THINKING_MODELS:
                kwargs["thinking"] = {"type": "adaptive"}
                logger.info(f"[LLM] Creating Anthropic LLM with ADAPTIVE THINKING: model={model}, max_tokens={max_tokens}")
            else:
                kwargs["thinking"] = {"type": "enabled", "budget_tokens": 8000}
                logger.info(f"[LLM] Creating Anthropic LLM with ENABLED THINKING: model={model}, budget=8000, max_tokens={max_tokens}")
            kwargs["max_tokens"] = max_tokens
            kwargs["temperature"] = 1
        else:
            kwargs["temperature"] = temperature
            kwargs["max_tokens"] = max_tokens
            logger.info(f"[LLM] Creating Anthropic LLM: model={model}, temp={temperature}, max_tokens={max_tokens}")

        return ChatAnthropic(**kwargs)
    elif provider == "openai":
        from langchain_openai import ChatOpenAI

        logger.info(f"[LLM] Creating OpenAI LLM: model={model}, temp={temperature}, max_tokens={max_tokens}")
        return ChatOpenAI(
            model=model,
            api_key=settings.openai_api_key or os.getenv("OPENAI_API_KEY"),
            temperature=temperature,
            max_tokens=max_tokens,
        )
    elif provider == "groq":
        from langchain_groq import ChatGroq

        return ChatGroq(
            model=model,
            api_key=settings.groq_api_key or os.getenv("GROQ_API_KEY"),
            temperature=temperature,
            max_tokens=max_tokens,
        )
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")
