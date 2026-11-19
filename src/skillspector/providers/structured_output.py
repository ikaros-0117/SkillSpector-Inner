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

"""Model rules for structured output on OpenAI-compatible endpoints.

Some Claude models reject forced tool calls. Compatible gateways can also
translate a JSON-schema response format into a forced call. For these models,
the providers bind the schema as a tool with automatic tool choice and ask
for the call in the prompt. The same shape can recover from an HTTP 400
rejecting JSON-schema output on other compatible endpoints.
"""

from __future__ import annotations

import re

from skillspector.providers import registry

# Bare model names documented to answer a forced tool call with HTTP 400.
FORCED_TOOL_CALL_REJECTED_MODELS = (
    "claude-fable-5-1",
    "claude-mythos-5-1",
    "claude-opus-5-5",
    "claude-sonnet-5-5",
)

# Bare model names documented to reject sampling controls (``temperature``) with HTTP 400.
# Each entry also covers its dated snapshots (``claude-opus-5-20260901``) and later
# point releases of the family (``claude-fable-5`` covers ``claude-fable-5-1``).
SAMPLING_REJECTED_MODELS = (
    "claude-opus-4-7",
    "claude-opus-4-8",
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-fable-5",
    "claude-mythos-5",
    "claude-haiku-5",
)

# A Claude model name ending an identifier, at its start or after a ``-``/``.``
# routing prefix (``bedrock-claude-...``, ``us.anthropic.claude-...``).
_CLAUDE_MODEL_NAME = re.compile(r"(?:^|[-.])(claude-[a-z0-9][a-z0-9.-]*)$")
_DOTTED_VERSION = re.compile(r"(?<=\d)\.(?=\d)")
# GPT-6.1 Sol, bare or after a routing prefix (``openai.gpt-6.1-sol``),
# optionally with a snapshot suffix (``gpt-6.1-sol-2026-09-15``).
_GPT_6_1_SOL = re.compile(r"(?:^|[-.])gpt-6\.1-sol(?:-.*)?$")


def model_name(model: str) -> str:
    """Return *model*'s last path segment, lowercased and cut at any ``@`` or ``:``.

    ``openai/gpt-6.1-sol:nitro`` and ``azure/anthropic/claude-opus-5-5@20260922``
    read as ``gpt-6.1-sol`` and ``claude-opus-5-5``.
    """
    return re.split(r"[@:]", model.rpartition("/")[2].lower(), maxsplit=1)[0]


def is_gpt_6_1_sol(model: str) -> bool:
    """Return ``True`` when *model* names GPT-6.1 Sol, bare or behind a gateway ID."""
    return _GPT_6_1_SOL.search(model_name(model)) is not None


def _names_model(model: str, names: tuple[str, ...]) -> bool:
    name = claude_model_name(model)
    return name is not None and any(name == bare or name.startswith(bare + "-") for bare in names)


def rejects_forced_tool_call(model: str) -> bool:
    """Return ``True`` when *model* names a Claude model that rejects forced tool calls."""
    return _names_model(model, FORCED_TOOL_CALL_REJECTED_MODELS)


def rejects_sampling_controls(model: str) -> bool:
    """Return whether *model* names a Claude model that rejects temperature."""
    return _names_model(model, SAMPLING_REJECTED_MODELS)


def forced_tool_choice_supported(model: str, registry_path: str) -> bool:
    """``False`` when *model* answers a forced tool call with HTTP 400.

    A ``tool_choice`` entry in the registry at *registry_path* wins (``auto``
    means the model must not be forced); otherwise the Claude model name read
    from *model* decides.
    """
    declared = registry.lookup_setting(registry_path, model, "tool_choice")
    if declared:
        return declared != "auto"
    return not rejects_forced_tool_call(model)


def claude_model_name(model: str) -> str | None:
    """Return the bare Claude model name carried by *model*, or ``None``.

    Recognizes gateway labels such as ``azure/anthropic/claude-opus-5-5``
    and normalizes dotted versions such as ``claude-opus-5.5``.
    """
    match = _CLAUDE_MODEL_NAME.search(model_name(model))
    return _DOTTED_VERSION.sub("-", match.group(1)) if match else None
