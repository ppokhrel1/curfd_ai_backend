import os

from langchain_core.language_models import BaseChatModel

from app.core.config import settings

_DEFAULTS = {
    "gemini": "gemini-2.5-flash",
    "openai": "gpt-4o",
    "anthropic": "claude-sonnet-4-20250514",
}


def get_llm() -> BaseChatModel:
    """Return a LangChain chat model based on settings.llm_provider."""
    provider = settings.llm_provider.lower()
    model = settings.llm_model or _DEFAULTS.get(provider)
    temperature = settings.llm_temperature

    if provider == "gemini":
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
        )
    elif provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=model,
            api_key=settings.anthropic_api_key or os.getenv("ANTHROPIC_API_KEY"),
            temperature=temperature,
        )
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")
