import logging
import os
from functools import lru_cache

from langchain_core.language_models import BaseChatModel

from app.core.config import settings

logger = logging.getLogger(__name__)

_DEFAULTS = {
    "groq": "llama-3.3-70b-versatile",
    "gemini": "gemini-2.5-flash",
    "openai": "gpt-4o",
    "anthropic": "claude-opus-4-6",
    "openrouter": "anthropic/claude-sonnet-4",
}

# Providers that support extended thinking
_THINKING_PROVIDERS = frozenset({"anthropic"})


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

    if provider == "groq":
        from langchain_groq import ChatGroq

        return ChatGroq(
            model=model,
            api_key=settings.groq_api_key or os.getenv("GROQ_API_KEY"),
            temperature=temperature,
        )
    elif provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=model,
            google_api_key=settings.gemini_api_key or os.getenv("GEMINI_API_KEY"),
            temperature=temperature,
        )
    elif provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=model,
            api_key=settings.openai_api_key or os.getenv("OPENAI_API_KEY"),
            temperature=temperature,
            max_tokens=max_tokens,
        )
    elif provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        import langchain_anthropic

        lc_version = getattr(langchain_anthropic, "__version__", "0.0.0")
        logger.info(f"[LLM] langchain-anthropic version: {lc_version}")

        kwargs: dict = {
            "model": model,
            "api_key": settings.anthropic_api_key or os.getenv("ANTHROPIC_API_KEY"),
        }
        if thinking:
            try:
                from packaging.version import Version
                supports_thinking = Version(lc_version) >= Version("0.3.0")
            except Exception:
                supports_thinking = hasattr(ChatAnthropic, "model_fields") and "thinking" in ChatAnthropic.model_fields

            if supports_thinking:
                kwargs["thinking"] = {"type": "enabled", "budget_tokens": 8_000}
            else:
                kwargs["model_kwargs"] = {"thinking": {"type": "enabled", "budget_tokens": 8_000}}
                logger.warning("[LLM] Old langchain-anthropic — using model_kwargs for thinking. Run: pip install 'langchain-anthropic>=0.3' --upgrade")
            kwargs["max_tokens"] = max_tokens
            kwargs["temperature"] = 1
            logger.info(f"[LLM] Creating Anthropic LLM with THINKING ENABLED: model={model}, max_tokens={max_tokens}")
        else:
            kwargs["temperature"] = temperature
            kwargs["max_tokens"] = max_tokens
            logger.info(f"[LLM] Creating Anthropic LLM: model={model}, temp={temperature}, max_tokens={max_tokens}")

        return ChatAnthropic(**kwargs)
    elif provider == "openrouter":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=model,
            api_key=settings.openrouter_api_key or os.getenv("OPENROUTER_API_KEY"),
            base_url="https://openrouter.ai/api/v1",
            temperature=temperature,
            max_tokens=max_tokens,
            default_headers={
                "HTTP-Referer": settings.frontend_url or "https://nooriat.com",
                "X-Title": "CURFD AI",
            },
        )
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")
