# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""OpenAI and OpenAI-compatible LLM providers.

Select a provider with ``SKILLSPECTOR_PROVIDER``. The default is ``openai``.
"""

from __future__ import annotations

import os
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING, NoReturn

from .base import (
    ChatModelProvider,
    CredentialsProvider,
    LLMProvider,
    ModelMetadataProvider,
)

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

NO_LLM_API_KEY_MESSAGE = (
    "No LLM API key configured. Set OPENAI_API_KEY (with optional OPENAI_BASE_URL), "
    "or configure SKILLSPECTOR_COMPAT_API_KEY and SKILLSPECTOR_COMPAT_BASE_URL "
    "for SKILLSPECTOR_PROVIDER=openai_compatible. "
    "Use --no-llm to skip LLM analysis and run static checks only."
)

_INJECTED_PROVIDER: ContextVar[LLMProvider | None] = ContextVar(
    "skillspector_injected_provider",
    default=None,
)


class UnknownProviderError(ValueError):
    """The configured provider name does not identify a supported provider."""


def raise_no_llm_api_key_configured() -> NoReturn:
    """Raise the shared no-LLM-credentials error."""
    raise ValueError(NO_LLM_API_KEY_MESSAGE)


def use_provider(provider: LLMProvider) -> Token[LLMProvider | None]:
    """Bind *provider* for the current context."""
    return _INJECTED_PROVIDER.set(provider)


def reset_provider(token: Token[LLMProvider | None]) -> None:
    """Restore the provider binding represented by *token*."""
    _INJECTED_PROVIDER.reset(token)


def has_provider_binding() -> bool:
    """Return whether the current context has an injected provider."""
    return _INJECTED_PROVIDER.get() is not None


def _select_active_provider() -> LLMProvider:
    """Construct the active provider based on ``SKILLSPECTOR_PROVIDER``."""
    injected_provider = _INJECTED_PROVIDER.get()
    if injected_provider is not None:
        return injected_provider

    name = os.environ.get("SKILLSPECTOR_PROVIDER", "").strip().lower()
    if name in ("", "openai"):
        return _openai_provider()
    if name == "openai_compatible":
        from .openai_compatible import OpenAICompatibleProvider

        return OpenAICompatibleProvider()

    raise UnknownProviderError(
        f"Unknown SKILLSPECTOR_PROVIDER: {name!r}. "
        "Expected one of: openai, openai_compatible (or unset)."
    )


def get_metadata_provider() -> ModelMetadataProvider:
    """Return the active provider for token-budget + default-model lookups."""
    return _select_active_provider()


def get_active_provider() -> ModelMetadataProvider:
    """Return the active provider (alias for :func:`get_metadata_provider`)."""
    return _select_active_provider()


def resolve_provider_credentials() -> tuple[str, str | None] | None:
    """Return the active provider's credentials when configured."""
    return _select_active_provider().resolve_credentials()


def _openai_provider() -> LLMProvider:
    """Construct the OpenAI provider without an eager package import."""
    from .openai import OpenAIProvider

    return OpenAIProvider()


def resolve_chat_model_credentials() -> tuple[str, str | None] | None:
    """Return credentials used for chat model construction, including fallback."""
    creds = resolve_provider_credentials()
    if creds is not None:
        return creds

    if has_provider_binding():
        return None

    return _openai_provider().resolve_credentials()


def get_model_config_provider() -> LLMProvider:
    """Return the provider whose model defaults match graph chat-model routing."""
    provider = _select_active_provider()
    if has_provider_binding():
        return provider
    try:
        if provider.resolve_credentials() is not None:
            return provider
    except ValueError:
        return provider

    fallback = _openai_provider()
    if fallback.resolve_credentials() is not None:
        return fallback
    return provider


def prepare_chat_model_imports() -> None:
    """Prepare only an opted-in, configured transport without creating a client."""
    provider = get_model_config_provider()
    prepare = getattr(provider, "prepare_chat_model_imports", None)
    if callable(prepare) and provider.resolve_credentials() is not None:
        prepare()


def create_chat_model_with_provider(
    model: str,
    *,
    max_tokens: int,
    timeout: float | None = 120,
) -> tuple[BaseChatModel, LLMProvider]:
    """Create a chat model and return the provider that actually built it.

    An unconfigured compatible provider falls back to standard OpenAI credentials.
    An explicitly injected provider remains authoritative.
    """
    provider = _select_active_provider()
    llm = provider.create_chat_model(model, max_tokens=max_tokens, timeout=timeout)
    if llm is not None:
        return llm, provider

    from .openai import OpenAIProvider

    if not has_provider_binding() and not isinstance(provider, OpenAIProvider):
        fallback = _openai_provider()
        llm = fallback.create_chat_model(model, max_tokens=max_tokens, timeout=timeout)
        if llm is not None:
            return llm, fallback

    raise_no_llm_api_key_configured()


def create_chat_model(
    model: str,
    *,
    max_tokens: int,
    timeout: float | None = 120,
) -> BaseChatModel:
    """Create the active provider's native LangChain chat model."""
    llm, _provider = create_chat_model_with_provider(
        model,
        max_tokens=max_tokens,
        timeout=timeout,
    )
    return llm


__all__ = [
    "ChatModelProvider",
    "CredentialsProvider",
    "LLMProvider",
    "ModelMetadataProvider",
    "NO_LLM_API_KEY_MESSAGE",
    "UnknownProviderError",
    "create_chat_model",
    "create_chat_model_with_provider",
    "get_active_provider",
    "get_model_config_provider",
    "prepare_chat_model_imports",
    "get_metadata_provider",
    "has_provider_binding",
    "reset_provider",
    "raise_no_llm_api_key_configured",
    "resolve_chat_model_credentials",
    "resolve_provider_credentials",
    "use_provider",
]
