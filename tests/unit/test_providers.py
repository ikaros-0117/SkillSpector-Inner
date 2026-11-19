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

"""Tests for provider selection, OpenAI clients, and model registries."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

import skillspector.providers as providers_module
from skillspector.providers import (
    NO_LLM_API_KEY_MESSAGE,
    chat_models,
    create_chat_model,
    get_active_provider,
    get_metadata_provider,
    has_provider_binding,
    registry,
    reset_provider,
    resolve_chat_model_credentials,
    resolve_provider_credentials,
    use_provider,
)
from skillspector.providers.chat_models import create_openai_compatible_chat_model
from skillspector.providers.openai import OpenAIProvider
from skillspector.providers.openai_compatible import OpenAICompatibleProvider


class FakeProvider:
    DEFAULT_MODEL = "fake-default"
    SLOT_DEFAULTS = {"meta_analyzer": "fake-meta"}

    def __init__(
        self,
        name: str,
        *,
        credentials: tuple[str, str | None] | None = None,
        chat_model: object | None = None,
    ) -> None:
        self.name = name
        self._credentials = credentials
        self.chat_model = chat_model if chat_model is not None else object()

    def get_context_length(self, model: str) -> int | None:
        return 111 if model == self.name else None

    def get_max_output_tokens(self, model: str) -> int | None:
        return 222 if model == self.name else None

    def resolve_model(self, slot: str = "default") -> str:
        return f"{self.name}:{slot}"

    def resolve_credentials(self) -> tuple[str, str | None] | None:
        return self._credentials

    def create_chat_model(
        self,
        model: str,
        *,
        max_tokens: int,
        timeout: float | None = 120,
    ) -> object:
        self.last_chat_model_request = (model, max_tokens, timeout)
        return self.chat_model


@pytest.mark.parametrize(
    ("name", "credentials"),
    [
        ("openai", {"OPENAI_API_KEY": "test"}),
        (
            "openai_compatible",
            {
                "SKILLSPECTOR_COMPAT_API_KEY": "test",
                "SKILLSPECTOR_COMPAT_BASE_URL": "https://example.invalid/v1",
            },
        ),
    ],
)
def test_prepare_transport_imports_without_creating_a_client(
    monkeypatch: pytest.MonkeyPatch, name: str, credentials: dict[str, str]
) -> None:
    monkeypatch.setenv("SKILLSPECTOR_PROVIDER", name)
    for key, value in credentials.items():
        monkeypatch.setenv(key, value)
    imports: list[str] = []
    monkeypatch.setattr(chat_models, "import_module", imports.append)

    def unexpected_client(*args, **kwargs):
        pytest.fail("Import preparation must not construct an unused client")

    monkeypatch.setattr(chat_models, "ChatOpenAI", unexpected_client)
    providers_module.prepare_chat_model_imports()

    assert imports == ["httpcore", "openai.resources.chat.completions.completions"]


@pytest.mark.parametrize("name", ["openai", "openai_compatible"])
def test_prepare_transport_imports_skips_unconfigured_bound_provider(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.setenv("SKILLSPECTOR_PROVIDER", name)
    for key in (
        "SKILLSPECTOR_COMPAT_API_KEY",
        "SKILLSPECTOR_COMPAT_BASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    imports: list[str] = []
    monkeypatch.setattr(chat_models, "import_module", imports.append)

    token = use_provider(get_active_provider())
    try:
        providers_module.prepare_chat_model_imports()
    finally:
        reset_provider(token)

    assert imports == []


def test_prepare_transport_imports_uses_actual_unbound_openai_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenAICompatibleProvider()
    monkeypatch.setattr(providers_module, "_select_active_provider", lambda: provider)
    monkeypatch.setenv("OPENAI_API_KEY", "test-fallback")
    imports: list[str] = []
    monkeypatch.setattr(chat_models, "import_module", imports.append)
    monkeypatch.setattr(
        provider, "prepare_chat_model_imports", lambda: pytest.fail("Wrong transport")
    )

    providers_module.prepare_chat_model_imports()

    assert imports == ["httpcore", "openai.resources.chat.completions.completions"]


def test_prepare_transport_imports_does_not_fall_back_for_explicit_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-fallback")
    imports: list[str] = []
    monkeypatch.setattr(chat_models, "import_module", imports.append)

    token = use_provider(OpenAICompatibleProvider())
    try:
        providers_module.prepare_chat_model_imports()
    finally:
        reset_provider(token)

    assert imports == []


def test_prepare_transport_imports_does_not_probe_providers_without_the_hook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = FakeProvider("custom")
    token = use_provider(provider)
    try:
        monkeypatch.setattr(
            provider, "resolve_credentials", lambda: pytest.fail("Unexpected credential probe")
        )
        providers_module.prepare_chat_model_imports()
    finally:
        reset_provider(token)


def test_prepare_transport_import_failure_does_not_construct_a_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SKILLSPECTOR_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    def failed_import(name: str):
        raise ImportError(name)

    monkeypatch.setattr(chat_models, "import_module", failed_import)
    with pytest.raises(ImportError, match="httpcore"):
        providers_module.prepare_chat_model_imports()


@pytest.fixture(autouse=True)
def _clean_provider_env(monkeypatch: pytest.MonkeyPatch):
    """Isolate provider-related env vars and the YAML cache for each test."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_PROJECT_ID", raising=False)
    monkeypatch.delenv("SKILLSPECTOR_REASONING_EFFORT", raising=False)
    monkeypatch.delenv("SKILLSPECTOR_TEMPERATURE", raising=False)
    monkeypatch.delenv("SKILLSPECTOR_SEED", raising=False)
    monkeypatch.delenv("SKILLSPECTOR_MODEL", raising=False)
    monkeypatch.delenv("SKILLSPECTOR_MODEL_REGISTRY", raising=False)
    monkeypatch.delenv("SKILLSPECTOR_PROVIDER", raising=False)
    providers_module._INJECTED_PROVIDER.set(None)
    for name in (
        "SKILLSPECTOR_COMPAT_API_KEY",
        "SKILLSPECTOR_COMPAT_BASE_URL",
        "SKILLSPECTOR_COMPAT_HEADERS",
    ):
        monkeypatch.delenv(name, raising=False)
    registry._load.cache_clear()
    yield
    providers_module._INJECTED_PROVIDER.set(None)
    registry._load.cache_clear()


class TestOpenAIProvider:
    """Stock OpenAI provider — credentials + bundled YAML metadata."""

    def test_returns_none_without_env_var(self) -> None:
        assert OpenAIProvider().resolve_credentials() is None

    def test_resolves_to_openai_with_default_base_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
        creds = OpenAIProvider().resolve_credentials()
        assert creds == ("sk-x", None)  # None → ChatOpenAI uses api.openai.com

    def test_honors_openai_base_url_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
        monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
        creds = OpenAIProvider().resolve_credentials()
        assert creds == ("sk-x", "http://localhost:11434/v1")

    def test_creates_chat_openai(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
        llm = OpenAIProvider().create_chat_model("gpt-5.4", max_tokens=123)
        assert isinstance(llm, ChatOpenAI)
        assert llm.model_name == "gpt-5.4"
        assert llm.max_tokens == 123

    def test_openai_project_id_sets_default_header(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
        monkeypatch.setenv("OPENAI_PROJECT_ID", "proj_123")
        llm = OpenAIProvider().create_chat_model("gpt-5.4", max_tokens=123)
        assert isinstance(llm, ChatOpenAI)
        assert llm.default_headers == {"OpenAI-Project": "proj_123"}

    def test_default_model(self) -> None:
        provider = OpenAIProvider()
        assert provider.resolve_model() == "gpt-5.4"
        # All slots retain the previous default until live scanner acceptance.
        assert provider.resolve_model("meta_analyzer") == "gpt-5.4"
        assert provider.get_context_length(provider.DEFAULT_MODEL) == 1_050_000
        assert provider.get_max_output_tokens(provider.DEFAULT_MODEL) == 128_000

    def test_metadata_known_model(self) -> None:
        provider = OpenAIProvider()
        assert provider.get_context_length("gpt-5.4") == 1_050_000
        assert provider.get_max_output_tokens("gpt-5.4") == 128_000
        assert provider.get_context_length("gemini-3.5-flash") == 1_048_576
        assert provider.get_max_output_tokens("gemini-3.5-flash") == 65_536

    def test_metadata_gpt5_generation(self) -> None:
        provider = OpenAIProvider()
        for model in (
            "gpt-5",
            "gpt-5-mini",
            "gpt-5-nano",
            "gpt-5.1",
            "gpt-5.1-codex",
            "gpt-5.1-codex-max",
            "gpt-5.2",
        ):
            assert provider.get_context_length(model) == 400_000
            assert provider.get_max_output_tokens(model) == 128_000

    def test_metadata_flagship_generation(self) -> None:
        provider = OpenAIProvider()
        for model in (
            "gpt-5.5",
            "gpt-5.6-luna",
            "gpt-5.6-terra",
            "gpt-5.6-sol",
            "gpt-5.6",
            "gpt-6-astra",
            "gpt-6-sol",
            "gpt-6-luna",
            "gpt-6.1-sol",
        ):
            assert provider.get_context_length(model) == 1_050_000
            assert provider.get_max_output_tokens(model) == 128_000

    def test_each_model_is_mapped_once(self) -> None:
        """Every bundled registry maps each model ID exactly once.

        ``yaml.safe_load`` collapses a repeated key to its last mapping,
        so value-only lookup assertions stay green while the resolved
        budget becomes loader-dependent. Reading keys via ``yaml.compose``
        catches the duplication that lookup loops cannot.
        """
        root = Path(__file__).resolve().parents[2]
        registries = [
            root / "model_registry.yaml",
            *root.glob("src/skillspector/providers/*/model_registry.yaml"),
        ]
        assert registries, "expected at least one bundled model registry"
        for registry_path in registries:
            doc = yaml.compose(registry_path.read_text(encoding="utf-8"))
            models = next(v for k, v in doc.value if k.value == "models")
            keys = [k.value for k, v in models.value]
            assert len(keys) == len(set(keys)), (
                f"{registry_path.name} maps {[k for k in keys if keys.count(k) > 1]} twice"
            )


REPO_ROOT = Path(__file__).resolve().parents[2]
ROOT_MODEL_REGISTRY = REPO_ROOT / "model_registry.yaml"
MODEL_REGISTRIES = sorted(
    [ROOT_MODEL_REGISTRY]
    + list((REPO_ROOT / "src" / "skillspector" / "providers").glob("*/model_registry.yaml"))
)


def _model_keys_as_written(registry_path: Path) -> list[str]:
    """Return the ``models`` keys in document order, duplicates included.

    ``yaml.safe_load`` keeps only the last of a repeated key, so asserting on
    resolved budgets cannot see a model that was mapped twice. Composing the
    node tree preserves every key exactly as the file spells it.
    """
    document = yaml.compose(registry_path.read_text(encoding="utf-8"))
    for key_node, value_node in document.value:
        if key_node.value == "models":
            return [key.value for key, _ in value_node.value]
    raise AssertionError(f"{registry_path} has no top-level 'models' mapping")


class TestModelRegistryFiles:
    """Integrity of the shipped YAML registries themselves."""

    def test_registries_are_discovered(self) -> None:
        assert ROOT_MODEL_REGISTRY in MODEL_REGISTRIES
        assert {path.parent.name for path in MODEL_REGISTRIES if path != ROOT_MODEL_REGISTRY} == {
            "openai",
            "openai_compatible",
        }

    @pytest.mark.parametrize(
        "registry_path",
        MODEL_REGISTRIES,
        ids=lambda path: path.relative_to(REPO_ROOT).as_posix(),
    )
    def test_each_model_is_mapped_once(self, registry_path: Path) -> None:
        # Loaders disagree on duplicate keys — most keep the last mapping,
        # strict ones reject the document — so mapping one model twice makes
        # the resolved budget loader-dependent and lets the copies drift apart
        # while value-only assertions stay green.
        keys = _model_keys_as_written(registry_path)
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        assert not duplicates, f"{registry_path} maps these models more than once: {duplicates}"

    def test_root_registry_replaces_bundled_yaml(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The repo-root file is the documented SKILLSPECTOR_MODEL_REGISTRY sample."""
        monkeypatch.setenv("SKILLSPECTOR_MODEL_REGISTRY", str(ROOT_MODEL_REGISTRY))
        provider = OpenAIProvider()

        # Gateway-prefixed ids that only the root registry carries.
        assert provider.get_context_length("openai/openai/gpt-5.3-chat") == 128_000
        assert provider.get_max_output_tokens("openai/openai/gpt-5.3-chat") == 16_384
        assert provider.get_context_length("azure/anthropic/claude-sonnet-4-6") == 1_000_000

        # Budgets the root registry must keep in step with the bundled YAML,
        # since an override replaces that file instead of merging with it.
        assert provider.get_context_length("gpt-5.6-sol") == 1_050_000
        assert provider.get_max_output_tokens("gpt-5.6-sol") == 128_000

        # Same replacement rule from the other side: a bundled-only model has
        # no budget at all while the override is in force.
        assert provider.get_context_length("gpt-6-astra") is None


class TestOpenAICompatibleConstructor:
    """The shared OpenAI-compatible chat-model constructor."""

    def test_returns_none_when_credentials_missing(self) -> None:
        assert (
            create_openai_compatible_chat_model(
                model="gpt-5.4",
                credentials=None,
                max_tokens=123,
            )
            is None
        )

    def test_builds_chat_openai_from_credentials(self) -> None:
        llm = create_openai_compatible_chat_model(
            model="gpt-5.4",
            credentials=("sk-x", "http://localhost:1234/v1"),
            max_tokens=123,
        )
        assert isinstance(llm, ChatOpenAI)
        assert llm.model_name == "gpt-5.4"
        assert llm.max_tokens == 123
        assert str(llm.openai_api_base).rstrip("/") == "http://localhost:1234/v1"

    def test_reasoning_effort_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, object] = {}

        def fake_chat_openai(**kwargs: object) -> dict[str, object]:
            captured.update(kwargs)
            return kwargs

        monkeypatch.setattr(chat_models, "ChatOpenAI", fake_chat_openai)
        monkeypatch.setenv("SKILLSPECTOR_REASONING_EFFORT", "  high  ")

        create_openai_compatible_chat_model(
            model="gpt-5.4",
            credentials=("sk-x", "http://localhost:1234/v1"),
            max_tokens=123,
        )

        assert captured["reasoning_effort"] == "high"

    def test_reasoning_effort_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, object] = {}

        def fake_chat_openai(**kwargs: object) -> dict[str, object]:
            captured.update(kwargs)
            return kwargs

        monkeypatch.setattr(chat_models, "ChatOpenAI", fake_chat_openai)

        create_openai_compatible_chat_model(
            model="gpt-5.4",
            credentials=("sk-x", "http://localhost:1234/v1"),
            max_tokens=123,
        )

        assert "reasoning_effort" not in captured
        assert captured["max_completion_tokens"] == 123

    @pytest.mark.parametrize("blank_value", ["   ", "\t\n"])
    def test_reasoning_effort_blank(
        self, monkeypatch: pytest.MonkeyPatch, blank_value: str
    ) -> None:
        captured: dict[str, object] = {}

        def fake_chat_openai(**kwargs: object) -> dict[str, object]:
            captured.update(kwargs)
            return kwargs

        monkeypatch.setattr(chat_models, "ChatOpenAI", fake_chat_openai)
        monkeypatch.setenv("SKILLSPECTOR_REASONING_EFFORT", blank_value)

        create_openai_compatible_chat_model(
            model="gpt-5.4",
            credentials=("sk-x", "http://localhost:1234/v1"),
            max_tokens=123,
        )

        assert "reasoning_effort" not in captured
        assert captured["max_completion_tokens"] == 123

    def test_reasoning_effort_provider_matrix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, object] = {}

        def fake_chat_openai(**kwargs: object) -> dict[str, object]:
            captured.clear()
            captured.update(kwargs)
            return kwargs

        monkeypatch.setattr(chat_models, "ChatOpenAI", fake_chat_openai)
        cases = (
            (OpenAIProvider(), "OPENAI_API_KEY", "sk-x", "http://localhost:1234/v1"),
            (
                OpenAICompatibleProvider(),
                "SKILLSPECTOR_COMPAT_API_KEY",
                "gateway-key",
                "https://gateway.example/v1",
            ),
        )
        for provider, key, value, endpoint in cases:
            monkeypatch.setenv(key, value)
            if isinstance(provider, OpenAIProvider):
                monkeypatch.setenv("OPENAI_BASE_URL", endpoint)
                monkeypatch.setenv("OPENAI_PROJECT_ID", "proj_123")
            else:
                monkeypatch.setenv("SKILLSPECTOR_COMPAT_BASE_URL", endpoint)
            for effort in (None, "   ", " high "):
                if effort is None:
                    monkeypatch.delenv("SKILLSPECTOR_REASONING_EFFORT", raising=False)
                else:
                    monkeypatch.setenv("SKILLSPECTOR_REASONING_EFFORT", effort)
                provider.create_chat_model("model-x", max_tokens=123)
                assert captured["base_url"] == endpoint
                assert captured["max_completion_tokens"] == 123
                assert isinstance(captured["api_key"], SecretStr)
                assert captured["api_key"].get_secret_value() == value
                if isinstance(provider, OpenAIProvider):
                    assert captured["default_headers"] == {"OpenAI-Project": "proj_123"}
                if effort is None or not effort.strip():
                    assert "reasoning_effort" not in captured
                else:
                    assert captured["reasoning_effort"] == "high"

    def test_reasoning_effort_passthrough(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, object] = {}

        def fake_chat_openai(**kwargs: object) -> dict[str, object]:
            captured.update(kwargs)
            return kwargs

        monkeypatch.setattr(chat_models, "ChatOpenAI", fake_chat_openai)
        monkeypatch.setenv("SKILLSPECTOR_REASONING_EFFORT", "provider-specific-value")

        create_openai_compatible_chat_model(
            model="gpt-5.4",
            credentials=("sk-x", "http://localhost:1234/v1"),
            max_tokens=123,
        )

        assert captured["reasoning_effort"] == "provider-specific-value"

    def test_sampling_controls_are_forwarded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, object] = {}

        def fake_chat_openai(**kwargs: object) -> dict[str, object]:
            captured.update(kwargs)
            return kwargs

        monkeypatch.setattr(chat_models, "ChatOpenAI", fake_chat_openai)
        monkeypatch.setenv("SKILLSPECTOR_TEMPERATURE", " 0.25 ")
        monkeypatch.setenv("SKILLSPECTOR_SEED", "42")

        create_openai_compatible_chat_model(
            model="gpt-5.4",
            credentials=("sk-x", "http://localhost:1234/v1"),
            max_tokens=123,
        )

        assert captured["temperature"] == 0.25
        assert captured["seed"] == 42

    @pytest.mark.parametrize("seed", [-(1 << 63), (1 << 63) - 1])
    def test_signed_64_bit_seed_boundaries_are_forwarded(
        self,
        monkeypatch: pytest.MonkeyPatch,
        seed: int,
    ) -> None:
        captured: dict[str, object] = {}

        def fake_chat_openai(**kwargs: object) -> dict[str, object]:
            captured.update(kwargs)
            return kwargs

        monkeypatch.setattr(chat_models, "ChatOpenAI", fake_chat_openai)
        monkeypatch.setenv("SKILLSPECTOR_SEED", str(seed))

        create_openai_compatible_chat_model(
            model="gpt-5.4",
            credentials=("sk-x", "http://localhost:1234/v1"),
            max_tokens=123,
        )

        assert captured["seed"] == seed

    @pytest.mark.parametrize(
        ("name", "value", "message"),
        [
            ("SKILLSPECTOR_TEMPERATURE", "warm", "must be a number"),
            ("SKILLSPECTOR_TEMPERATURE", "1.1", "must be between 0 and 1"),
            ("SKILLSPECTOR_SEED", "4.2", "must be an integer"),
            ("SKILLSPECTOR_SEED", str(1 << 63), "must be a signed 64-bit integer"),
            ("SKILLSPECTOR_SEED", str(-(1 << 63) - 1), "must be a signed 64-bit integer"),
        ],
    )
    def test_invalid_sampling_control_fails_before_model_construction(
        self,
        monkeypatch: pytest.MonkeyPatch,
        name: str,
        value: str,
        message: str,
    ) -> None:
        monkeypatch.setenv(name, value)
        with pytest.raises(ValueError, match=message):
            create_openai_compatible_chat_model(
                model="gpt-5.4",
                credentials=("sk-x", "http://localhost:1234/v1"),
                max_tokens=123,
            )


class TestProviderSelection:
    """SKILLSPECTOR_PROVIDER selects which provider answers credentials."""

    def test_no_env_defaults_to_openai(self) -> None:
        assert isinstance(get_metadata_provider(), OpenAIProvider)
        assert resolve_provider_credentials() is None

    def test_select_openai(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SKILLSPECTOR_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
        monkeypatch.setenv("SKILLSPECTOR_COMPAT_API_KEY", "should-be-ignored")
        monkeypatch.setenv("SKILLSPECTOR_COMPAT_BASE_URL", "https://gateway.example/v1")
        creds = resolve_provider_credentials()
        assert creds == ("sk-x", None)
        assert isinstance(get_metadata_provider(), OpenAIProvider)

    def test_chat_model_credentials_fall_back_to_openai(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SKILLSPECTOR_PROVIDER", "openai_compatible")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
        creds = resolve_chat_model_credentials()
        assert creds == ("sk-x", None)

    def test_unknown_provider_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SKILLSPECTOR_PROVIDER", "vertex")
        with pytest.raises(ValueError, match="Unknown SKILLSPECTOR_PROVIDER"):
            get_metadata_provider()

    def test_create_chat_model_falls_back_to_openai_when_provider_unconfigured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SKILLSPECTOR_PROVIDER", "openai_compatible")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
        llm = create_chat_model("gpt-5.4", max_tokens=123)
        assert isinstance(llm, ChatOpenAI)
        assert llm.model_name == "gpt-5.4"

    def test_create_chat_model_raises_when_no_credentials_anywhere(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SKILLSPECTOR_PROVIDER", "openai_compatible")
        with pytest.raises(ValueError) as exc_info:
            create_chat_model("claude-opus-4-6", max_tokens=123)
        assert str(exc_info.value) == NO_LLM_API_KEY_MESSAGE

    def test_create_chat_model_raises_for_openai_provider_without_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # When the active provider is already OpenAI, there is no second
        # fallback attempt — it raises directly.
        monkeypatch.setenv("SKILLSPECTOR_PROVIDER", "openai")
        with pytest.raises(ValueError) as exc_info:
            create_chat_model("gpt-5.4", max_tokens=123)
        assert str(exc_info.value) == NO_LLM_API_KEY_MESSAGE

    def test_injected_provider_routes_metadata_and_active_helpers(self) -> None:
        provider = FakeProvider("injected")
        token = use_provider(provider)
        try:
            assert has_provider_binding() is True
            assert get_metadata_provider() is provider
            assert get_active_provider() is provider
        finally:
            reset_provider(token)
        assert has_provider_binding() is False

    def test_injected_provider_routes_credentials_and_chat_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SKILLSPECTOR_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
        chat_model = object()
        provider = FakeProvider(
            "injected",
            credentials=("injected-key", "injected-base-url"),
            chat_model=chat_model,
        )
        token = use_provider(provider)
        try:
            assert resolve_provider_credentials() == ("injected-key", "injected-base-url")
            assert create_chat_model("model-x", max_tokens=42) is chat_model
        finally:
            reset_provider(token)

    def test_provider_token_reset_restores_env_dispatch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SKILLSPECTOR_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
        provider = FakeProvider("injected", credentials=("injected-key", None))
        token = use_provider(provider)
        reset_provider(token)
        assert isinstance(get_metadata_provider(), OpenAIProvider)
        assert resolve_provider_credentials() == ("sk-x", None)

    def test_provider_token_nested_restores_previous_binding(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SKILLSPECTOR_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
        outer_provider = FakeProvider(
            "outer",
            credentials=("outer-key", "outer-base-url"),
        )
        inner_provider = FakeProvider(
            "inner",
            credentials=("inner-key", "inner-base-url"),
        )
        outer_token = use_provider(outer_provider)
        try:
            inner_token = use_provider(inner_provider)
            try:
                assert get_metadata_provider() is inner_provider
                assert resolve_provider_credentials() == ("inner-key", "inner-base-url")
            finally:
                reset_provider(inner_token)
            assert get_metadata_provider() is outer_provider
            assert resolve_provider_credentials() == ("outer-key", "outer-base-url")
        finally:
            reset_provider(outer_token)
        assert isinstance(get_metadata_provider(), OpenAIProvider)
        assert resolve_provider_credentials() == ("sk-x", None)
