# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Static scans never resolve provider credentials."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from skillspector.cli import app
from skillspector.nodes.report import _build_metadata
from skillspector.providers import reset_provider, use_provider
from skillspector.providers.openai_compatible import OpenAICompatibleProvider
from tests.graph_scan import run_graph_scan


@pytest.fixture(params=[False, True], ids=["unbound", "bound"])
def credential_lookups(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> Iterator[list[object]]:
    """Exercise both environment selection and explicit provider binding."""
    monkeypatch.setenv("SKILLSPECTOR_PROVIDER", "openai_compatible")
    lookups: list[object] = []

    def record_lookup(provider: OpenAICompatibleProvider) -> None:
        lookups.append(provider)

    monkeypatch.setattr(OpenAICompatibleProvider, "resolve_credentials", record_lookup)
    token = use_provider(OpenAICompatibleProvider()) if request.param else None
    try:
        yield lookups
    finally:
        if token is not None:
            reset_provider(token)


@pytest.mark.parametrize("output_format", ["json", "markdown", "sarif", "terminal"])
def test_cli_static_scan_never_resolves_provider_credentials(
    tmp_path: Path,
    safe_skill_dir: Path,
    credential_lookups: list[object],
    output_format: str,
) -> None:
    output = tmp_path / "report.txt"

    result = CliRunner().invoke(
        app,
        ["scan", str(safe_skill_dir), "--no-llm", "--format", output_format, "-o", str(output)],
    )

    assert result.exit_code == 0, result.output
    assert output.read_text(encoding="utf-8")
    assert credential_lookups == []
    if output_format == "json":
        metadata = json.loads(output.read_text(encoding="utf-8"))["metadata"]
        assert metadata["llm_requested"] is False
        assert metadata["llm_available"] is False


@pytest.mark.parametrize("output_format", ["json", "markdown", "sarif", "terminal"])
async def test_graph_static_scan_never_resolves_provider_credentials(
    safe_skill_dir: Path,
    credential_lookups: list[object],
    output_format: str,
) -> None:

    result = await run_graph_scan(str(safe_skill_dir), use_llm=False, output_format=output_format)

    assert result["report_body"]
    assert result["llm_requested"] is False
    if output_format == "json":
        metadata = json.loads(result["report_body"])["metadata"]
        assert metadata["llm_available"] is False
    assert credential_lookups == []


def test_static_report_metadata_never_resolves_provider_credentials(
    credential_lookups: list[object],
) -> None:

    metadata = _build_metadata(has_executable_scripts=False, use_llm=False)

    assert metadata["llm_requested"] is False
    assert metadata["llm_available"] is False
    assert credential_lookups == []
