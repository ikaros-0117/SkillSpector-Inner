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

"""Tests for the OpenAI-compatible provider."""

from __future__ import annotations

import json
import traceback
from unittest.mock import patch

import httpx
import pytest
from langchain_openai import ChatOpenAI

from skillspector.providers import (
    get_metadata_provider,
    registry,
    resolve_provider_credentials,
)
from skillspector.providers.openai_compatible import OpenAICompatibleProvider


@pytest.fixture(autouse=True)
def _clean_provider_env(monkeypatch: pytest.MonkeyPatch):
    """Isolate provider-related env vars and the YAML cache for each test."""
    for key in (
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "SKILLSPECTOR_MODEL",
        "SKILLSPECTOR_MODEL_REGISTRY",
        "SKILLSPECTOR_PROVIDER",
        "SKILLSPECTOR_COMPAT_API_KEY",
        "SKILLSPECTOR_COMPAT_BASE_URL",
        "SKILLSPECTOR_COMPAT_HEADERS",
        "SKILLSPECTOR_TEMPERATURE",
        "SKILLSPECTOR_SEED",
    ):
        monkeypatch.delenv(key, raising=False)
    registry._load.cache_clear()
    yield
    registry._load.cache_clear()


class TestOpenAICompatibleProvider:
    """Generic OpenAI-compatible provider — Groq, Together AI, Mistral, etc."""

    def test_returns_none_without_env_vars(self) -> None:
        assert OpenAICompatibleProvider().resolve_credentials() is None

    def test_returns_none_with_key_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SKILLSPECTOR_COMPAT_API_KEY", "gsk_abc")
        assert OpenAICompatibleProvider().resolve_credentials() is None

    def test_returns_none_with_url_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SKILLSPECTOR_COMPAT_BASE_URL", "https://api.groq.com/openai/v1")
        assert OpenAICompatibleProvider().resolve_credentials() is None

    def test_resolves_with_both_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SKILLSPECTOR_COMPAT_API_KEY", "gsk_abc")
        monkeypatch.setenv("SKILLSPECTOR_COMPAT_BASE_URL", "https://api.groq.com/openai/v1")
        creds = OpenAICompatibleProvider().resolve_credentials()
        assert creds == ("gsk_abc", "https://api.groq.com/openai/v1")

    def test_creates_chat_openai(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SKILLSPECTOR_COMPAT_API_KEY", "gsk_abc")
        monkeypatch.setenv("SKILLSPECTOR_COMPAT_BASE_URL", "https://api.groq.com/openai/v1")
        llm = OpenAICompatibleProvider().create_chat_model(
            "llama-3.1-70b-versatile", max_tokens=1024
        )
        assert isinstance(llm, ChatOpenAI)
        assert llm.model_name == "llama-3.1-70b-versatile"
        assert str(llm.openai_api_base).rstrip("/") == "https://api.groq.com/openai/v1"

    def test_default_model(self) -> None:
        assert OpenAICompatibleProvider().resolve_model() == "llama-3.1-70b-versatile"

    def test_metadata_known_model(self) -> None:
        provider = OpenAICompatibleProvider()
        assert provider.get_context_length("llama-3.1-70b-versatile") == 131072
        assert provider.get_max_output_tokens("llama-3.1-70b-versatile") == 8192

    def test_metadata_unknown_model_returns_none(self) -> None:
        provider = OpenAICompatibleProvider()
        assert provider.get_context_length("some-random-model") is None

    def test_create_returns_none_without_credentials(self) -> None:
        assert (
            OpenAICompatibleProvider().create_chat_model("llama-3.1-70b-versatile", max_tokens=1024)
            is None
        )

    def test_forced_tool_choice_is_kept_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SKILLSPECTOR_COMPAT_API_KEY", "gsk_abc")
        monkeypatch.setenv("SKILLSPECTOR_COMPAT_BASE_URL", "https://api.groq.com/openai/v1")
        provider = OpenAICompatibleProvider()
        llm = provider.create_chat_model("llama-3.1-70b-versatile", max_tokens=1024)
        assert isinstance(llm, ChatOpenAI)
        assert llm.disabled_params is None
        assert provider.forced_tool_choice_supported("llama-3.1-70b-versatile")
        assert provider.structured_output_method("llama-3.1-70b-versatile") is None

    def test_tool_choice_auto_entry_disables_forced_tool_choice(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SKILLSPECTOR_COMPAT_API_KEY", "sk-abc")
        monkeypatch.setenv(
            "SKILLSPECTOR_COMPAT_BASE_URL", "https://maas-token-api.cn-huabei-1.xf-yun.com/v2"
        )
        provider = OpenAICompatibleProvider()
        llm = provider.create_chat_model("spark-x2.5", max_tokens=1024)
        assert isinstance(llm, ChatOpenAI)
        assert llm.disabled_params == {"tool_choice": None}
        assert not provider.forced_tool_choice_supported("spark-x2.5")
        assert provider.structured_output_method("spark-x2.5") == "function_calling"
        assert provider.get_context_length("spark-x2.5") == 262144

    def test_registry_override_can_declare_tool_choice_auto(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        override = tmp_path / "registry.yaml"
        override.write_text(
            "models:\n  my-model:\n    context_length: 32768\n    tool_choice: auto\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("SKILLSPECTOR_MODEL_REGISTRY", str(override))
        provider = OpenAICompatibleProvider()
        assert not provider.forced_tool_choice_supported("my-model")
        assert provider.structured_output_method("my-model") == "function_calling"

    def test_declared_structured_output_method_wins(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        override = tmp_path / "registry.yaml"
        override.write_text(
            "models:\n  my-model:\n    context_length: 32768\n"
            "    tool_choice: auto\n    structured_output: json_schema\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("SKILLSPECTOR_MODEL_REGISTRY", str(override))
        assert OpenAICompatibleProvider().structured_output_method("my-model") == "json_schema"


class TestOpenAICompatibleHeaders:
    """Custom gateway headers reach both HTTP clients without leaking on errors."""

    @pytest.fixture(autouse=True)
    def _configure_endpoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SKILLSPECTOR_COMPAT_API_KEY", "synthetic-api-key")
        monkeypatch.setenv("SKILLSPECTOR_COMPAT_BASE_URL", "https://gateway.example.com/v1")

    @pytest.mark.parametrize("raw_headers", [None, "", " \t\r\n", "{}"])
    def test_empty_headers(self, monkeypatch: pytest.MonkeyPatch, raw_headers: str | None) -> None:
        if raw_headers is not None:
            monkeypatch.setenv("SKILLSPECTOR_COMPAT_HEADERS", raw_headers)
        llm = OpenAICompatibleProvider().create_chat_model("gateway-model", max_tokens=1024)
        assert isinstance(llm, ChatOpenAI)
        assert llm.default_headers == ({} if raw_headers == "{}" else None)

    def test_parses_string_headers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        headers = {"X-Tenant-ID": "my-tenant", "X-API-Key": "synthetic-gateway-key"}
        monkeypatch.setenv("SKILLSPECTOR_COMPAT_HEADERS", f"  {json.dumps(headers)}  ")
        llm = OpenAICompatibleProvider().create_chat_model("gateway-model", max_tokens=1024)
        assert isinstance(llm, ChatOpenAI)
        assert llm.default_headers == headers

    @pytest.mark.parametrize(
        "raw_headers",
        [
            "not-json",
            '{"X-API-Key":"synthetic-gateway-secret",}',
            "[]",
            "null",
            "true",
            "42",
            '"synthetic-gateway-secret"',
            '{"X-API-Key":7}',
            '{"X-API-Key":true}',
            '{"X-API-Key":null}',
            '{"X-API-Key":["synthetic-gateway-secret"]}',
            '{"X-API-Key":{"nested":"synthetic-gateway-secret"}}',
        ],
    )
    def test_invalid_headers_do_not_expose_values(
        self, monkeypatch: pytest.MonkeyPatch, raw_headers: str
    ) -> None:
        monkeypatch.setenv("SKILLSPECTOR_COMPAT_HEADERS", raw_headers)
        with pytest.raises(ValueError) as exc_info:
            OpenAICompatibleProvider().create_chat_model("gateway-model", max_tokens=1024)
        assert str(exc_info.value) == (
            "SKILLSPECTOR_COMPAT_HEADERS must be a JSON object with string keys and values"
        )
        assert "synthetic-gateway-secret" not in "".join(traceback.format_exception(exc_info.value))

    @pytest.mark.parametrize("asynchronous", [False, True])
    async def test_headers_reach_http_requests(
        self, monkeypatch: pytest.MonkeyPatch, asynchronous: bool
    ) -> None:
        headers = {"X-Tenant-ID": "my-tenant", "X-API-Key": "synthetic-gateway-key"}
        monkeypatch.setenv("SKILLSPECTOR_COMPAT_HEADERS", json.dumps(headers))
        requests: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                json={
                    "id": "chatcmpl-test",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "gateway-model",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": "ok"},
                        }
                    ],
                },
            )

        with httpx.Client(transport=httpx.MockTransport(respond)) as sync_client:
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as async_client:
                with patch(
                    "skillspector.providers.chat_models.ChatOpenAI",
                    side_effect=lambda **kwargs: ChatOpenAI(
                        http_client=sync_client, http_async_client=async_client, **kwargs
                    ),
                ):
                    llm = OpenAICompatibleProvider().create_chat_model(
                        "gateway-model", max_tokens=1024
                    )
                assert isinstance(llm, ChatOpenAI)
                response = await llm.ainvoke("hello") if asynchronous else llm.invoke("hello")
                assert response.content == "ok"
        assert len(requests) == 1
        assert requests[0].url == "https://gateway.example.com/v1/chat/completions"
        for key, value in headers.items():
            assert requests[0].headers[key] == value


class TestOpenAICompatibleProviderSelection:
    """SKILLSPECTOR_PROVIDER=openai_compatible selects the generic provider."""

    def test_select_openai_compatible(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SKILLSPECTOR_PROVIDER", "openai_compatible")
        assert isinstance(get_metadata_provider(), OpenAICompatibleProvider)

    def test_compat_credentials_via_selector(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SKILLSPECTOR_PROVIDER", "openai_compatible")
        monkeypatch.setenv("SKILLSPECTOR_COMPAT_API_KEY", "gsk_abc")
        monkeypatch.setenv("SKILLSPECTOR_COMPAT_BASE_URL", "https://api.groq.com/openai/v1")
        creds = resolve_provider_credentials()
        assert creds == ("gsk_abc", "https://api.groq.com/openai/v1")


class TestUnknownProviderError:
    def test_error_message_lists_supported_providers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SKILLSPECTOR_PROVIDER", "nonexistent")
        with pytest.raises(
            ValueError, match=r"Expected one of: openai, openai_compatible \(or unset\)"
        ):
            get_metadata_provider()
