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

"""Tests for the LLM credential resolution in llm_utils.

Order: active SkillSpector provider -> OPENAI_API_KEY / OPENAI_BASE_URL.
Provider-specific behavior (which env var resolves to which client) lives
in the active provider — see ``tests/unit/test_providers.py``.
"""

from __future__ import annotations

import asyncio

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration
from pydantic import BaseModel

from skillspector import llm_utils
from skillspector.constants import build_model_config
from skillspector.llm_utils import (
    StructuredOutputParseError,
    _resolve_llm_credentials,
    bind_structured_output,
    chat_completion,
    fetch_model_token_limits,
    get_chat_model,
    is_llm_available,
    run_async,
    structured_output_kwargs,
)
from skillspector.providers import (
    NO_LLM_API_KEY_MESSAGE,
    reset_provider,
    resolve_chat_model_credentials,
    resolve_provider_credentials,
    use_provider,
)
from skillspector.providers.openai_compatible import OpenAICompatibleProvider

_LLM_ENV_VARS = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "SKILLSPECTOR_COMPAT_API_KEY",
    "SKILLSPECTOR_COMPAT_BASE_URL",
    "SKILLSPECTOR_REASONING_EFFORT",
    "SKILLSPECTOR_MODEL",
    "SKILLSPECTOR_PROVIDER",
)


@pytest.fixture(autouse=True)
def _clean_llm_env(monkeypatch: pytest.MonkeyPatch):
    """Clear all LLM-related env vars for test isolation."""
    for var in _LLM_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    yield


class TestCredentialResolution:
    """Order: active provider first, then OPENAI_API_KEY / OPENAI_BASE_URL."""

    def test_provider_wins_when_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SKILLSPECTOR_PROVIDER", "openai_compatible")
        monkeypatch.setenv("SKILLSPECTOR_COMPAT_API_KEY", "gateway-key")
        monkeypatch.setenv("SKILLSPECTOR_COMPAT_BASE_URL", "https://gateway.example/v1")
        monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
        provider_creds = resolve_provider_credentials()
        assert provider_creds is not None  # active provider must answer
        key, base = _resolve_llm_credentials()
        assert key == "gateway-key"
        assert base == provider_creds[1]

    def test_openai_used_when_provider_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
        key, base = _resolve_llm_credentials()
        assert key == "openai-key"
        assert base is None

    def test_openai_base_url_used_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
        monkeypatch.setenv("OPENAI_BASE_URL", "http://openai.example/v1")
        _, base = _resolve_llm_credentials()
        assert base == "http://openai.example/v1"

    def test_provider_base_url_not_overridden_by_openai_base_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OPENAI_BASE_URL is the OpenAI tier; it does not affect the provider tier."""
        monkeypatch.setenv("SKILLSPECTOR_PROVIDER", "openai_compatible")
        monkeypatch.setenv("SKILLSPECTOR_COMPAT_API_KEY", "gateway-key")
        monkeypatch.setenv("SKILLSPECTOR_COMPAT_BASE_URL", "https://gateway.example/v1")
        monkeypatch.setenv("OPENAI_BASE_URL", "http://openai.example/v1")
        provider_creds = resolve_provider_credentials()
        assert provider_creds is not None
        _, base = _resolve_llm_credentials()
        assert base == provider_creds[1]

    def test_no_credentials_raises_with_helpful_message(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            _resolve_llm_credentials()
        assert str(exc_info.value) == NO_LLM_API_KEY_MESSAGE

    def test_injected_provider_without_credentials_builds_native_chat_model(self) -> None:
        chat_model = object()

        class _InjectedProvider:
            DEFAULT_MODEL = "injected-default"
            SLOT_DEFAULTS = {"meta_analyzer": "injected-default"}

            def get_context_length(self, model: str) -> int | None:
                return 4096 if model == "injected-default" else None

            def get_max_output_tokens(self, model: str) -> int | None:
                return 128 if model == "injected-default" else None

            def resolve_model(self, slot: str = "default") -> str:
                return "injected-default"

            def resolve_credentials(self) -> tuple[str, str | None] | None:
                return None

            def create_chat_model(
                self,
                model: str,
                *,
                max_tokens: int,
                timeout: float | None = 120,
            ) -> object:
                assert model == "injected-default"
                assert max_tokens == 128
                assert timeout == 120
                return chat_model

        token = use_provider(_InjectedProvider())
        try:
            assert is_llm_available() == (True, None)
            assert get_chat_model() is chat_model
        finally:
            reset_provider(token)

    def test_injected_provider_without_native_model_does_not_fall_back_to_openai(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fallback")

        class _InjectedProvider:
            DEFAULT_MODEL = "injected-default"
            SLOT_DEFAULTS = {}

            def get_context_length(self, model: str) -> int | None:
                return 4096

            def get_max_output_tokens(self, model: str) -> int | None:
                return 128

            def resolve_model(self, slot: str = "default") -> str:
                return "injected-default"

            def resolve_credentials(self) -> tuple[str, str | None] | None:
                return None

            def create_chat_model(
                self,
                model: str,
                *,
                max_tokens: int,
                timeout: float | None = 120,
            ) -> object | None:
                return None

        token = use_provider(_InjectedProvider())
        try:
            assert resolve_chat_model_credentials() is None
            assert is_llm_available() == (False, NO_LLM_API_KEY_MESSAGE)
            with pytest.raises(ValueError) as exc_info:
                get_chat_model()
            assert str(exc_info.value) == NO_LLM_API_KEY_MESSAGE
        finally:
            reset_provider(token)


class TestFetchModelTokenLimits:
    def test_returns_input_and_output_token_pair(self) -> None:
        max_input, max_output = fetch_model_token_limits("claude-opus-4-6")
        assert isinstance(max_input, int)
        assert isinstance(max_output, int)
        assert max_input > 0
        assert max_output > 0


class TestChatCompletion:
    """``chat_completion`` invokes the active chat model and normalizes content."""

    def test_returns_string_content_directly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _FakeLLM:
            def invoke(self, prompt: str) -> AIMessage:
                assert prompt == "ping"
                return AIMessage(content="hello world")

        monkeypatch.setattr(
            llm_utils, "get_chat_model", lambda model=None, timeout=None: _FakeLLM()
        )
        assert chat_completion("ping") == "hello world"

    def test_returns_text_from_langchain_content_blocks(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _FakeLLM:
            def invoke(self, prompt: str) -> AIMessage:
                return AIMessage(content=[{"type": "text", "text": "chunk"}])

        captured: dict[str, str | None] = {}

        def _fake_get_chat_model(
            model: str | None = None, timeout: float | None = None
        ) -> _FakeLLM:
            captured["model"] = model
            return _FakeLLM()

        monkeypatch.setattr(llm_utils, "get_chat_model", _fake_get_chat_model)
        result = chat_completion("prompt", model="some-model")
        assert result == "chunk"
        assert captured["model"] == "some-model"

    def test_returns_empty_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _FakeLLM:
            def invoke(self, prompt: str) -> AIMessage:
                return AIMessage(content="")

        monkeypatch.setattr(
            llm_utils, "get_chat_model", lambda model=None, timeout=None: _FakeLLM()
        )
        assert chat_completion("prompt") == ""


class TestIsLlmAvailable:
    def test_returns_true_when_credentials_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "k")
        ok, msg = is_llm_available()
        assert ok is True
        assert msg is None

    def test_returns_true_via_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SKILLSPECTOR_PROVIDER", "openai_compatible")
        monkeypatch.setenv("SKILLSPECTOR_COMPAT_API_KEY", "k")
        monkeypatch.setenv("SKILLSPECTOR_COMPAT_BASE_URL", "https://gateway.example/v1")
        ok, msg = is_llm_available()
        assert ok is True
        assert msg is None

    def test_returns_false_with_message_when_no_credentials(self) -> None:
        ok, msg = is_llm_available()
        assert ok is False
        assert msg == NO_LLM_API_KEY_MESSAGE


class TestGetChatModel:
    def test_openai_fallback_uses_openai_default_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SKILLSPECTOR_PROVIDER", "openai_compatible")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai-only")

        llm = get_chat_model()

        assert _chat_model_name(llm) == "gpt-5.4"

    def test_graph_model_config_matches_openai_fallback_client(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SKILLSPECTOR_PROVIDER", "openai_compatible")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai-only")

        model = build_model_config()["default"]
        llm = get_chat_model(model=model)

        assert model == "gpt-5.4"
        assert _chat_model_name(llm) == "gpt-5.4"

    def test_explicit_model_still_overrides_openai_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SKILLSPECTOR_PROVIDER", "openai_compatible")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai-only")

        llm = get_chat_model(model="custom/model")

        assert _chat_model_name(llm) == "custom/model"

    def test_provider_credentials_use_provider_default_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SKILLSPECTOR_PROVIDER", "openai_compatible")
        monkeypatch.setenv("SKILLSPECTOR_COMPAT_API_KEY", "gateway-key")
        monkeypatch.setenv("SKILLSPECTOR_COMPAT_BASE_URL", "https://gateway.example/v1")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")

        llm = get_chat_model()

        assert _chat_model_name(llm) == OpenAICompatibleProvider.DEFAULT_MODEL


def _chat_model_name(llm: object) -> str:
    return str(getattr(llm, "model_name", None) or getattr(llm, "model", None))


class TestRunAsync:
    """Tests for run_async helper function that handles nested event loops."""

    async def _test_async_function(self, value: int, delay: float = 0) -> int:
        """Simple async function for testing."""
        if delay > 0:
            await asyncio.sleep(delay)
        return value * 2

    async def _test_async_function_raises(self) -> None:
        """Async function that raises an exception for testing."""
        raise ValueError("Test exception")

    def test_run_async_without_running_loop(self) -> None:
        """Test run_async works correctly when there is no running event loop."""
        result = run_async(self._test_async_function(42))
        assert result == 84

    def test_run_async_with_running_loop(self) -> None:
        """Test run_async works correctly even when there is already a running event loop.

        This regression test covers the scenario where SkillSpector is invoked from
        environments like Jupyter Notebooks, FastAPI, or LangGraph Studio that already
        have an active event loop.
        """

        async def _test_in_running_loop() -> int:
            # Call run_async from within an already running event loop
            return run_async(self._test_async_function(100))

        # Use asyncio.run to create a running loop context
        result = asyncio.run(_test_in_running_loop())
        assert result == 200

    def test_run_async_propagates_exceptions(self) -> None:
        """Test exceptions from async functions are properly propagated."""
        with pytest.raises(ValueError, match="Test exception"):
            run_async(self._test_async_function_raises())

    def test_run_async_with_delay(self) -> None:
        """Test run_async correctly handles async functions with await calls."""
        result = run_async(self._test_async_function(5, delay=0.01))
        assert result == 10


class _HintingProvider:
    def structured_output_method(self, model: str) -> str | None:
        return "json_schema" if model.startswith("needs-json-") else None


class _PlainProvider:
    pass


class Verdict(BaseModel):
    summary: str


def _stub_chat_openai(
    monkeypatch: pytest.MonkeyPatch, answers: list[AIMessage]
) -> tuple[object, list[tuple[str, dict]]]:
    """A ``ChatOpenAI`` with ``tool_choice`` disabled whose transport returns *answers* in order."""
    from langchain_core.outputs import ChatResult
    from langchain_openai import ChatOpenAI

    requests: list[tuple[str, dict]] = []

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        requests.append((messages[-1].content, kwargs))
        return ChatResult(generations=[ChatGeneration(message=answers.pop(0))])

    monkeypatch.setattr(ChatOpenAI, "_generate", _generate)
    llm = ChatOpenAI(
        model="spark-x2.5",
        api_key="sk-test",
        base_url="https://maas-token-api.cn-huabei-1.xf-yun.com/v2",
        disabled_params={"tool_choice": None},
    )
    return llm, requests


class _RecordingLLM:
    def __init__(self) -> None:
        self.calls: list[tuple[type, dict]] = []

    def with_structured_output(self, schema: type, **kwargs):
        self.calls.append((schema, kwargs))
        return self


class TestStructuredOutputMethod:
    """``with_structured_output`` binding follows env > provider hint > LangChain default."""

    def test_default_when_provider_has_no_hint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SKILLSPECTOR_STRUCTURED_OUTPUT_METHOD", raising=False)
        assert structured_output_kwargs("any-model", provider=_PlainProvider()) == {}

    def test_provider_hint_selects_json_schema(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SKILLSPECTOR_STRUCTURED_OUTPUT_METHOD", raising=False)
        assert structured_output_kwargs("needs-json-1", provider=_HintingProvider()) == {
            "method": "json_schema"
        }
        assert structured_output_kwargs("other", provider=_HintingProvider()) == {}

    def test_analyzer_preference_applies_only_without_explicit_configuration(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SKILLSPECTOR_STRUCTURED_OUTPUT_METHOD", raising=False)
        assert structured_output_kwargs(
            "other", provider=_PlainProvider(), preferred_method="function_calling"
        ) == {"method": "function_calling"}
        assert structured_output_kwargs(
            "needs-json-1", provider=_HintingProvider(), preferred_method="function_calling"
        ) == {"method": "json_schema"}
        monkeypatch.setenv("SKILLSPECTOR_STRUCTURED_OUTPUT_METHOD", "json_schema")
        assert structured_output_kwargs(
            "other", provider=_PlainProvider(), preferred_method="function_calling"
        ) == {"method": "json_schema"}

    @pytest.mark.parametrize("override", [None, "json_schema", "function_calling"])
    @pytest.mark.parametrize("provider", [_PlainProvider(), _HintingProvider()])
    def test_unknown_analyzer_preference_is_rejected(
        self, monkeypatch: pytest.MonkeyPatch, override, provider
    ) -> None:
        monkeypatch.delenv("SKILLSPECTOR_STRUCTURED_OUTPUT_METHOD", raising=False)
        if override is not None:
            monkeypatch.setenv("SKILLSPECTOR_STRUCTURED_OUTPUT_METHOD", override)
        with pytest.raises(ValueError, match="preferred structured output method"):
            structured_output_kwargs("needs-json-1", provider=provider, preferred_method="xml")

    def test_selected_function_calling_rejects_none_without_changing_prompt(self, monkeypatch):
        from langchain_core.runnables import RunnableLambda

        monkeypatch.setenv("SKILLSPECTOR_STRUCTURED_OUTPUT_METHOD", "function_calling")
        prompts = []

        class Model:
            def with_structured_output(self, schema, **kwargs):
                assert kwargs == {"method": "function_calling"}

                def answer(prompt):
                    prompts.append(prompt)
                    return None

                return RunnableLambda(answer)

        chain = bind_structured_output(Model(), dict, "m", provider=_PlainProvider())
        with pytest.raises(StructuredOutputParseError):
            chain.invoke("unchanged prompt")
        assert prompts == ["unchanged prompt"]

    def test_env_override_wins_over_the_hint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SKILLSPECTOR_STRUCTURED_OUTPUT_METHOD", "function_calling")
        assert structured_output_kwargs("needs-json-1", provider=_HintingProvider()) == {
            "method": "function_calling"
        }
        monkeypatch.setenv("SKILLSPECTOR_STRUCTURED_OUTPUT_METHOD", " JSON_SCHEMA ")
        assert structured_output_kwargs("other", provider=_PlainProvider()) == {
            "method": "json_schema"
        }

    def test_unknown_env_value_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SKILLSPECTOR_STRUCTURED_OUTPUT_METHOD", "xml")
        with pytest.raises(ValueError, match="SKILLSPECTOR_STRUCTURED_OUTPUT_METHOD"):
            structured_output_kwargs("any", provider=_PlainProvider())

    def test_bind_passes_the_method_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SKILLSPECTOR_STRUCTURED_OUTPUT_METHOD", raising=False)
        llm = _RecordingLLM()
        bind_structured_output(llm, dict, "needs-json-1", provider=_HintingProvider())
        bind_structured_output(llm, dict, "plain", provider=_HintingProvider())
        assert llm.calls == [(dict, {"method": "json_schema"}), (dict, {})]

    def test_forced_tool_choice_models_are_bound_untouched(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SKILLSPECTOR_STRUCTURED_OUTPUT_METHOD", raising=False)
        llm = _RecordingLLM()
        assert bind_structured_output(llm, dict, "m", provider=_PlainProvider()) is llm

    def test_chat_openai_with_tool_choice_disabled_asks_for_the_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from skillspector.providers.openai_compatible import OpenAICompatibleProvider

        monkeypatch.delenv("SKILLSPECTOR_STRUCTURED_OUTPUT_METHOD", raising=False)
        monkeypatch.delenv("SKILLSPECTOR_MODEL_REGISTRY", raising=False)
        llm, requests = _stub_chat_openai(
            monkeypatch,
            [
                AIMessage(content="Verdict: looks fine."),
                AIMessage(
                    content="",
                    tool_calls=[{"name": "Verdict", "args": {"summary": "ok"}, "id": "call_1"}],
                ),
            ],
        )
        chain = bind_structured_output(
            llm, Verdict, "spark-x2.5", provider=OpenAICompatibleProvider()
        )
        with pytest.raises(StructuredOutputParseError, match="Verdict"):
            chain.invoke("analyse this")  # type: ignore[attr-defined]
        assert chain.invoke("analyse this") == Verdict(summary="ok")  # type: ignore[attr-defined]
        prompt, request = requests[0]
        assert prompt.startswith("analyse this\n\n") and "calling the Verdict tool" in prompt
        assert "tool_choice" not in request
        assert request["tools"][0]["function"]["name"] == "Verdict"

    @pytest.mark.parametrize("method", [None, "json_schema"])
    def test_chat_openai_with_tool_choice_disabled_keeps_a_non_tool_method(
        self, monkeypatch: pytest.MonkeyPatch, method: str | None
    ) -> None:
        """Without an explicit ``function_calling`` the binder sends ``response_format`` unwrapped."""
        if method:
            monkeypatch.setenv("SKILLSPECTOR_STRUCTURED_OUTPUT_METHOD", method)
        else:
            monkeypatch.delenv("SKILLSPECTOR_STRUCTURED_OUTPUT_METHOD", raising=False)
        llm, requests = _stub_chat_openai(
            monkeypatch,
            [
                AIMessage(
                    content='{"summary": "ok"}',
                    additional_kwargs={"parsed": Verdict(summary="ok")},
                )
            ],
        )
        chain = bind_structured_output(llm, Verdict, "spark-x2.5", provider=_PlainProvider())
        assert chain.invoke("analyse this") == Verdict(summary="ok")  # type: ignore[attr-defined]
        prompt, request = requests[0]
        assert prompt == "analyse this"
        assert request["response_format"]["type"] == "json_schema"
        assert request["response_format"]["json_schema"]["strict"] is True
        assert "tools" not in request
