"""LLM provider registry - Simplified for Ollama"""
import os
from urllib.parse import urlparse
from typing import List, Optional
from .base_provider import BaseLLMProvider
from .openai_provider import OpenAIProvider


LOCAL_OLLAMA_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}


def _is_local_ollama_base_url(target_url: str) -> bool:
    """True when Ollama endpoint is local to this container/host."""
    try:
        parsed = urlparse(target_url)
        return (parsed.hostname or "").lower() in LOCAL_OLLAMA_HOSTS
    except Exception:
        return False


def _get_cloud_run_token(target_url: str) -> str:
    """Genereer ID token voor Cloud Run service-to-service authenticatie.

    Werkt automatisch op Cloud Run via de metadata server.
    Lokaal werkt het met gcloud credentials (Application Default Credentials).
    """
    import google.auth.transport.requests
    import google.oauth2.id_token

    # Strip /v1 suffix om de audience correct te zetten
    audience = target_url.rstrip("/").removesuffix("/v1")
    auth_req = google.auth.transport.requests.Request()
    return google.oauth2.id_token.fetch_id_token(auth_req, audience)


def get_provider(
    provider_name: str,
    model: str = "gemma3:12b",
    api_key: Optional[str] = None,
    base_url: str = None,
) -> BaseLLMProvider:
    """Get Ollama provider (OpenAI-compatible)."""
    if base_url is None:
        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")

    architecture = os.getenv("ARCHITECTURE", "HOGENT")

    if api_key:
        resolved_api_key = api_key
    elif architecture == "CLOUD_RUN" and not _is_local_ollama_base_url(base_url):
        # Enkel nodig voor Cloud Run -> Cloud Run service-to-service calls.
        resolved_api_key = _get_cloud_run_token(base_url)
    else:
        # Lokale Ollama endpoint (single-container) gebruikt geen Google ID token.
        resolved_api_key = os.getenv("OLLAMA_API_KEY", "ollama")

    return OpenAIProvider(
        model=model,
        api_key=resolved_api_key,
        base_url=base_url,
        provider_name="ollama",
    )


def get_available_providers() -> List[str]:
    return ["ollama"]
