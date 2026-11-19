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

"""Live OSS provider endpoint tests."""

from __future__ import annotations

import os
import warnings

import pytest
from pydantic import BaseModel, Field

pytestmark = [
    pytest.mark.provider,
    pytest.mark.filterwarnings("ignore:Pydantic serializer warnings:UserWarning"),
]


class ProviderResult(BaseModel):
    """Tiny schema used to validate provider structured-output wiring."""

    ok: bool = Field(description="Whether the provider request succeeded.")


def _skip_without_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        message = f"{name} is not set; skipping this live provider test"
        warnings.warn(message, RuntimeWarning, stacklevel=2)
        pytest.skip(message)
    return value


def _model_from_env(name: str, default: str) -> str:
    return os.environ.get(name, "").strip() or default


def test_openai_provider_makes_live_structured_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OpenAI provider reaches its default endpoint and returns structured output."""
    from skillspector.llm_utils import bind_structured_output
    from skillspector.providers.openai import OpenAIProvider

    _skip_without_env("OPENAI_API_KEY")
    # This live provider check must hit OpenAI's default base URL, not a proxy.
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    model = _model_from_env("SKILLSPECTOR_OPENAI_TEST_MODEL", OpenAIProvider.DEFAULT_MODEL)
    provider = OpenAIProvider()
    # Reasoning models spend output tokens on reasoning, so leave headroom.
    llm = provider.create_chat_model(model, max_tokens=4096, timeout=120)
    assert llm is not None
    assert llm.openai_api_base is None

    # Bind like the analyzers do, so the provider's structured-output method applies.
    result = bind_structured_output(llm, ProviderResult, model, provider).invoke(
        "Return only the requested structured output with ok=true."
    )

    assert result == ProviderResult(ok=True)
