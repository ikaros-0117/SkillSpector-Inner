# SkillSpector

**Security scanner for AI agent skills.** Detect vulnerabilities, malicious patterns, and security risks before installing agent skills.

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://www.apache.org/licenses/LICENSE-2.0)

## Overview

AI agent skills (used by Claude Code, Codex CLI, Gemini CLI, etc.) execute with implicit trust and minimal vetting. In the 31,132-skill analyzed subset of the research dataset, **26.1% of skills contain vulnerabilities** and **5.2% show likely malicious intent**.

SkillSpector helps you answer: **"Is this skill safe to install?"**

SkillSpector is part of the [NVIDIA Verified Skills pipeline](https://docs.nvidia.com/skills/), which scans, evaluates, and signs agent skills before publication. Skills that pass are published to the [NVIDIA skills catalog](https://github.com/NVIDIA/skills).

## Documentation

- **[Scan agent skills before installation](https://docs.nvidia.com/skills/scanning-agent-skills)** — Hosted guide: when to scan, how to read a report, and how to gate installs.
- **[Development guide](docs/DEVELOPMENT.md)** — Architecture, package layout, and how to extend the analyzer pipeline.
- **[Security policy](SECURITY.md)** — Report suspected vulnerabilities privately to NVIDIA PSIRT.
- **[Analysis resource bounds](docs/ANALYSIS_RESOURCE_BOUNDS.md)** — Fail-closed bundle, parser, nested-artifact, ledger, and finding ceilings.

## Features

- **Multi-format input**: Scan Git repos, URLs, zip files, directories, or single files
- **71 vulnerability patterns** across 17 categories: prompt injection, data exfiltration, privilege escalation, supply chain, excessive agency, output handling, system prompt leakage, memory poisoning, tool misuse, rogue agent, anti-refusal, trigger abuse, dangerous code (AST), taint tracking, YARA signatures, MCP least privilege, and MCP tool poisoning
- **Two-stage analysis**: Fast static analysis + optional LLM semantic evaluation
- **Live vulnerability lookups**: SC4 queries [OSV.dev](https://osv.dev) for real-time CVE data with automatic offline fallback
- **Multiple output formats**: Terminal, JSON, Markdown, and SARIF reports
- **Risk scoring**: 0-100 score with severity labels and clear recommendations
- **Baseline / false-positive suppression**: Accept known findings via a glob-rule or fingerprint baseline so re-scans surface only *new* issues ([docs](docs/SUPPRESSION.md))

## Quick Start

### Installation

> **Open-source software notice:** This project will download and install additional third-party open source software projects. Review the license terms of these open source projects before use.

From this checkout, install the command with uv:

```bash
uv tool install .
```

For the [Python API](#python-api-integration), install into the virtual
environment your application uses. The Makefile uses uv when available and
falls back to pip.

```bash
uv venv .venv && source .venv/bin/activate
# Or use python3 -m venv .venv && source .venv/bin/activate.
make install
```

See [Development setup](#setup) for supported Python versions and test dependencies.

### Basic Usage

```bash
# Scan a local skill directory
skillspector scan ./my-skill/

# Scan a single SKILL.md file
skillspector scan ./SKILL.md

# Scan a Git repository
skillspector scan https://github.com/user/my-skill

# Scan a zip file
skillspector scan ./my-skill.zip
```

#### Size limits

SkillSpector enforces two independent caps on remote and archive inputs to bound the impact of oversized downloads and zip bombs:

- **Per-ingest cap**: `INGEST_MAX_BYTES` (100 MiB) — applied to streamed URL downloads, total uncompressed size of zip archives, and post-clone disk usage of Git repos.
- **Zip member cap**: `INGEST_MAX_ZIP_MEMBERS` (10,000) — caps the number of entries in a single zip.

Note that the per-file 1 MB analysis cap (`MAX_FILE_BYTES`) is a separate, downstream limit: it bounds what individual analyzers will read out of an already-ingested directory. The ingest caps above bound how much content can land on disk in the first place. A breach of either ingest cap fails closed with an `IngestLimitExceededError`.

### Output Formats

```bash
# Terminal output (default) - pretty formatted
skillspector scan ./my-skill/

# JSON output - machine readable
skillspector scan ./my-skill/ --format json --output report.json

# Markdown output - for documentation
skillspector scan ./my-skill/ --format markdown --output report.md

# SARIF output - for CI/CD integration and IDE tooling
skillspector scan ./my-skill/ --format sarif --output report.sarif
```

### Recursive Collection Scanning

`--recursive` scans immediate subdirectories containing `SKILL.md` as independent
skills, with a default aggregate limit of **32 skills**. To raise or lower it:

```bash
skillspector scan ./my-skills/ --recursive --max-skills 100 --no-llm --format json
```

`--max-skills` requires a local recursive collection and a positive integer; `0`
does not mean unlimited. Discovery, byte, finding-record, time and report-size
budgets still apply, so increasing the count does not guarantee complete coverage.
When the count limit is reached, the CLI names the limit and suggests adjusting it
or scanning sub-skills separately. Omitted skills remain visible in the aggregate
report and coverage stays incomplete. Use `--fail-on-incomplete` in CI to fail on
incomplete coverage; the default exit policy is unchanged.

### Comparing MCP Registry snapshots

A named scan fetches only that server's published versions from the official
registry, then selects entries marked as latest. If none are marked as latest,
all matching versions are assessed. The server name remains the report source:

```bash
skillspector scan 'ac.inference.sh/mcp' --mcp-registry --format json --output current.json
```

Use `https://registry.modelcontextprotocol.io/v0/servers` as the input to scan
the full catalog. Local registry captures are also supported.

Start with raw registry payload captures (`registry-before.json` and
`registry-after.json`). Save a scan report as `previous.json`, then compare a
later scan against that generated local report:

```bash
skillspector scan registry-before.json --mcp-registry --format json --output previous.json
skillspector scan registry-after.json --mcp-registry --format json \
  --mcp-registry-compare previous.json --output compared.json
```

The optional `comparison` object lists added and removed server identities,
changed normalized fields with their previous and current values, and an
`unchanged_count`. Identity is the server name and version, so a new version
appears as an addition and the old version as a removal if it is absent from the
new scan. Acquisition source and scan timestamp are excluded from comparison.
`unmodeled_changes` lists same-identity records whose raw-record hash changed
while normalized fields match; these are not counted as unchanged. This can
indicate a change to fields outside the snapshot model, or array reordering in
the raw record. Package and remote ordering alone is not a normalized field change.
Entries in `changed` may also contain changes outside the snapshot model; inspect
the raw record to see those changes.
Compare reports with the same selection scope: a server absent from the current
input is reported as removed, which does not prove it was removed from a registry.

The comparison file must be a local report produced by this registry mode with
valid normalized snapshots. Malformed, oversized or duplicate server identities
are rejected. This option requires `--mcp-registry` and is separate from the
finding-suppression `--baseline`: it never suppresses findings or changes risk
scores, and it does not fetch or execute listed server endpoints.

Registry mode rejects skill-only controls such as `--use-shipped-baseline`,
`--transitive`, `--transitive-depth`, `--transitive-allow-prefix`, and
`--transitive-deny-prefix` with exit code 2, including an explicitly supplied
default depth (`--transitive-depth 1`). Omit these options for registry scans.

### Suppressing False Positives (baseline)

Suppress known/accepted findings so the risk score reflects only un-triaged
issues and re-scans surface only *new* findings. See the
[suppression guide](docs/SUPPRESSION.md) for the full reference.

```bash
# Accept all current findings into a baseline (run once), then commit it.
skillspector baseline ./my-skill/ -o .skillspector-baseline.yaml

# Scan against the baseline — only NEW findings are reported and scored.
skillspector scan ./my-skill/ --baseline .skillspector-baseline.yaml

# Review what was suppressed (still excluded from the score).
skillspector scan ./my-skill/ --baseline .skillspector-baseline.yaml --show-suppressed
```

A baseline can also use drift-tolerant glob rules (by rule id, file path, or
message) — see [`.skillspector-baseline.example.yaml`](.skillspector-baseline.example.yaml).
Exact fingerprint baselines are evidence-bound: changing the scanned source or
SkillSpector version keeps the finding active until it is reviewed again.
When a selected baseline or baseline output is stored inside the skill
directory, SkillSpector excludes that exact file from content analysis so its
suppression text cannot create findings or enter regenerated fingerprints;
sibling files remain in normal scan scope.

### Explicit scan scope

For a local directory containing one `SKILL.md` or `skill.md`, repeat `--exclude` to omit
selected files before content analysis:

```bash
skillspector scan ./my-skill --exclude 'tests/*' --exclude 'fixtures/*.json'
```

Patterns are case-sensitive globs matched against the entire relative POSIX path;
`*` also matches `/`. Quote patterns so your shell does not expand them. This is
an explicit caller option, not an author-controlled ignore file. Patterns matching
either manifest name (`SKILL.md` or `skill.md`) are rejected, as are recursive, multi-skill, transitive, registry, and
non-directory inputs. No-match patterns are still recorded in the report.

Files already outside the scan inventory, such as policy-excluded dependencies,
retain their existing policy handling and are not counted as caller exclusions.
Explicitly excluded inventory files remain in the coverage denominator as entirely uninspected. The
report lists the applied patterns, excluded count and individual skipped paths;
any matched exclusion makes coverage partial and prevents a `SAFE` recommendation.
Use `--fail-on-incomplete` when partial scope should fail CI. These exclusions do
not suppress findings in included files or override existing safety limits.

### LLM Analysis

Set `SKILLSPECTOR_PROVIDER` to select an endpoint for semantic analysis.
It defaults to `openai`. Both providers support hosted gateways and local
servers that implement the OpenAI API, such as vLLM, Ollama, and llama.cpp.

| Provider (`SKILLSPECTOR_PROVIDER`) | Credential env var | Endpoint | Default model |
| ---------- | ---- | ---- | ---- |
| `openai` | `OPENAI_API_KEY` (+ optional `OPENAI_BASE_URL`) | api.openai.com (or any OpenAI-compatible URL) | `gpt-5.4` |
| `openai_compatible` | `SKILLSPECTOR_COMPAT_API_KEY` + `SKILLSPECTOR_COMPAT_BASE_URL` | Any OpenAI-compatible endpoint | `llama-3.1-70b-versatile` |

For `openai_compatible` gateways requiring extra HTTP headers, set
`SKILLSPECTOR_COMPAT_HEADERS='{"X-Tenant-ID":"my-tenant","X-API-Key":"gateway-key"}'`.
It must be a JSON object with string keys and values and applies to sync and async requests.
Unset or blank values add no headers; invalid configuration is rejected without exposing header values.
Treat this environment variable as a secret when it contains gateway API keys.

`gpt-6.1-sol`, `claude-opus-5-5`, and `claude-sonnet-5-5` remain opt-in through
`SKILLSPECTOR_MODEL` or per-slot overrides. Promoting them to defaults requires
live scanner acceptance covering benign input, credential disclosure, prompt
injection, refusals, and incomplete responses.
Record findings, completion status, latency, and usage, and compare against
previous defaults where accessible.

Structured output uses LangChain's `with_structured_output` and defaults to
`json_schema`. Both providers recognize gateway model IDs for Claude models
that reject forced tool calls, such as `anthropic/claude-opus-5-5`. For those
models, they bind the schema as a tool with automatic tool choice and retry
prose answers. A model registry entry with `tool_choice: auto` also enables
this behavior for models such as iFlytek's bundled `spark-x2.5`.
`SKILLSPECTOR_STRUCTURED_OUTPUT_METHOD=json_schema|function_calling`
overrides the method for any provider.
The method precedence is `SKILLSPECTOR_STRUCTURED_OUTPUT_METHOD`, the provider's
method hint, an analyzer preference, then the client default. TP4 prefers
`function_calling` only for provider `openai` and the exact model label
`azure/anthropic/claude-opus-5`, with reasoning/thinking controls unset; dated
or suffixed labels and other providers keep their existing behavior. An explicit
environment override or provider hint still wins when reasoning is configured.

```bash
# Stock OpenAI
export SKILLSPECTOR_PROVIDER=openai
export OPENAI_API_KEY=sk-...
skillspector scan ./my-skill/

# Any other OpenAI-compatible endpoint
export SKILLSPECTOR_PROVIDER=openai_compatible
export SKILLSPECTOR_COMPAT_API_KEY=...
export SKILLSPECTOR_COMPAT_BASE_URL=https://api.groq.com/openai/v1
export SKILLSPECTOR_MODEL=llama-3.1-70b-versatile
skillspector scan ./my-skill/

# Override the provider's default model
export SKILLSPECTOR_MODEL=gpt-5.2
skillspector scan ./my-skill/

# Skip LLM analysis (faster, static analysis only)
skillspector scan ./my-skill/ --no-llm
```

## Vulnerability Patterns

SkillSpector detects **71 vulnerability patterns** across 17 categories:

### Prompt Injection (6 patterns)

| ID | Pattern | Severity | Description |
|----|---------|----------|-------------|
| P1 | Instruction Override | HIGH | Commands to ignore safety constraints |
| P2 | Hidden Instructions | HIGH | Malicious directives in comments/invisible text |
| P3 | Exfiltration Commands | HIGH | Instructions to transmit context externally |
| P4 | Behavior Manipulation | MEDIUM | Subtle instructions altering agent decisions |
| P5 | Harmful Content | CRITICAL | Instructions that could cause physical harm |
| P9 | Whitespace Padding | MEDIUM | Large whitespace padding hiding instructions below/beside the visible area |

### Anti-Refusal (3 patterns)

| ID | Pattern | Severity | Description |
|----|---------|----------|-------------|
| AR1 | Refusal Suppression | HIGH | Instructions to never refuse or always comply (e.g. "never refuse", "always comply") |
| AR2 | Disclaimer Suppression | HIGH | Instructions to omit warnings, disclaimers, or ethical commentary (e.g. "no disclaimers", "do not moralize") |
| AR3 | Safety Policy Nullification | HIGH | Jailbreak framing that nullifies guardrails (e.g. "you have no restrictions", "ignore your guidelines", "do anything now") |

### Data Exfiltration (5 patterns)

| ID | Pattern | Severity | Description |
|----|---------|----------|-------------|
| E1 | External Transmission | MEDIUM | Sending data to external URLs |
| E2 | Env Variable Harvesting | HIGH | Enumerating, copying, or searching environment data to collect secrets. A copy traced only to a child process's `env=` is reported LOW |
| E3 | File System Enumeration | MEDIUM | Scanning directories for sensitive files |
| E4 | Context Leakage | HIGH | Transmitting conversation context externally |
| E5 | Cloud Storage Exfiltration | MEDIUM | Uploading data through cloud storage SDKs or CLIs |

Within bounded, fully observed context, E5 distinguishes literal AWS/GCS downloads
from uploads. Simple remote-to-local `cp`, AWS `sync`, and `gsutil rsync` commands do not produce E5
findings. Moves, deletion options, unknown flags, dynamic operands, streams and
compound shell commands retain conservative findings. This direction check does
not change SDK upload detection or the CLI's exit-code policy. Direction proofs
require the complete preceding context to fit within 4,096 characters. Only
blank/comment lines, literal `set` flags (`-e`, `-u`, `-f`, and `-o pipefail`),
and simple directory changes may precede a proved download. Other setup,
including sourcing, autoloading, variable assignments, and earlier transfers,
retains the original finding. Relative destinations after directory changes
remain unproved; literal absolute file destinations are still eligible.

### Privilege Escalation (3 patterns)

| ID | Pattern | Severity | Description |
|----|---------|----------|-------------|
| PE1 | Excessive Permissions | LOW | Requesting access beyond stated functionality |
| PE2 | Sudo/Root Execution | MEDIUM | Invoking elevated system privileges |
| PE3 | Credential Access | HIGH | Reading SSH keys, tokens, passwords |

### Supply Chain (10+ patterns)

| ID | Pattern | Severity | Description |
|----|---------|----------|-------------|
| SC1 | Unpinned Dependencies | LOW | No version constraints on packages |
| SC2 | External Script Fetching | HIGH | curl \| bash and remote code execution |
| SC3 | Obfuscated Code | HIGH | Base64/hex encoded execution |
| SC4 | Known Vulnerable Dependencies | HIGH | Dependencies with known CVEs (live OSV.dev lookup) |
| SC5 | Abandoned Dependencies | MEDIUM | Unmaintained packages without security updates |
| SC6 | Typosquatting | HIGH | Package names similar to popular packages |
| SC8 | Shipped Python Bytecode | HIGH | `__pycache__` / `.pyc` present (discovery skips; malicious bytecode bypass) |
| SC9 | Concealed Executable Artifact | HIGH | Executable nested in a document container or hidden/disguised artifact; root-level visible source helpers in manifested `.agents/skills/<name>/` and `.claude/skills/<name>/` folders are exempted but remain local-only and are marked as not LLM-reviewed |
| SC10 | Dependency Source Redirection | HIGH | Package-manager source added, replaced, or unresolved |

### Excessive Agency (5 patterns)

| ID | Pattern | Severity | Description |
|----|---------|----------|-------------|
| EA1 | Unrestricted Tool Access | HIGH | Unfettered tool access without constraints |
| EA2 | Autonomous Decision Making | HIGH | High-impact decisions without human-in-the-loop |
| EA3 | Scope Creep | MEDIUM | Capabilities extending beyond stated purpose |
| EA4 | Unbounded Resource Access | MEDIUM | No rate limits or quotas on resource consumption |
| EA5 | External Model or Provider Selection | MEDIUM/HIGH | Model/provider pins or coding-CLI shell-outs that can switch billing accounts |

### Output Handling (3 patterns)

| ID | Pattern | Severity | Description |
|----|---------|----------|-------------|
| OH1 | Unvalidated Output Injection | HIGH | Model output used without sanitization |
| OH2 | Cross-Context Output | MEDIUM | Output flows across trust boundaries without validation |
| OH3 | Unbounded Output | MEDIUM | No limits on output size or generation rate |

### System Prompt Leakage (3 patterns)

| ID | Pattern | Severity | Description |
|----|---------|----------|-------------|
| P6 | Direct Leakage | HIGH | Instructions that expose system prompts or internal rules |
| P7 | Indirect Extraction | MEDIUM | Extraction via rephrasing, translation, or side-channels |
| P8 | Tool-Based Exfiltration | HIGH | System prompts exfiltrated via file writes or network requests |

### Memory Poisoning (3 patterns)

| ID | Pattern | Severity | Description |
|----|---------|----------|-------------|
| MP1 | Persistent Context Injection | HIGH | Content designed to persist across interactions |
| MP2 | Context Window Stuffing | MEDIUM | Filler content displacing safety constraints |
| MP3 | Memory Manipulation | HIGH | Tampering with agent memory or stored state |

### Tool Misuse (3 patterns)

| ID | Pattern | Severity | Description |
|----|---------|----------|-------------|
| TM1 | Tool Parameter Abuse | HIGH | Crafted parameters for unintended behavior (shell=True, --force) |
| TM2 | Chaining Abuse | HIGH | Tool chains that bypass individual safety checks |
| TM3 | Unsafe Defaults | MEDIUM | Overly permissive defaults (disabled TLS, no auth) |

### Rogue Agent (2 patterns)

| ID | Pattern | Severity | Description |
|----|---------|----------|-------------|
| RA1 | Self-Modification | CRITICAL | Modifying own code or configuration at runtime |
| RA2 | Session Persistence | HIGH | Unauthorized persistence via cron jobs or startup scripts |

### Trigger Abuse (3 patterns)

| ID | Pattern | Severity | Description |
|----|---------|----------|-------------|
| TR1 | Overly Broad Trigger | MEDIUM | Trigger patterns matching common words |
| TR2 | Shadow Command Trigger | HIGH | Triggers that shadow built-in commands or other skills |
| TR3 | Keyword Baiting Trigger | MEDIUM | Generic triggers designed to maximize activation |

### Behavioral AST (9 patterns)

| ID | Pattern | Severity | Description |
|----|---------|----------|-------------|
| AST1 | exec() Call | CRITICAL | Direct exec() enabling arbitrary code execution |
| AST2 | eval() Call | HIGH | Direct eval() evaluating arbitrary expressions |
| AST3 | Dynamic Import | HIGH | \_\_import\_\_() loading arbitrary modules at runtime |
| AST4 | subprocess Call | HIGH | External command execution via subprocess |
| AST5 | os.system / exec-family | HIGH | Shell commands via os module |
| AST6 | compile() Call | MEDIUM | Code object creation from strings |
| AST7 | Dynamic getattr() | MEDIUM | Arbitrary attribute access with non-literal names |
| AST8 | Dangerous Execution Chain | CRITICAL | exec/eval combined with dynamic source (network, encoded data) |
| AST9 | Reflective getattr() Sink | HIGH | Reflective exec via `getattr(os,'system')` / `getattr(builtins,'exec')` that evades AST1/AST5 |

### Taint Tracking (5 patterns)

| ID | Pattern | Severity | Description |
|----|---------|----------|-------------|
| TT1 | Direct Taint Flow | HIGH | Data flows directly from a source to a sink without sanitization |
| TT2 | Variable-Mediated Taint Flow | MEDIUM | Data flows from source to sink through intermediate variables |
| TT3 | Credential Exfiltration Chain | CRITICAL | Credentials (env vars, secrets) flow to network output sinks |
| TT4 | File Read to Network Exfiltration | HIGH | File contents flow to network output sinks |
| TT5 | External Input to Code Execution | CRITICAL | Network or user input flows to exec/eval/subprocess sinks |

### YARA Signatures (4 patterns)

| ID | Pattern | Severity | Description |
|----|---------|----------|-------------|
| YR1 | Malware Match | CRITICAL | YARA rule match for known malware signatures |
| YR2 | Webshell Match | CRITICAL | YARA rule match for webshell patterns |
| YR3 | Cryptominer Match | HIGH | YARA rule match for crypto mining indicators |
| YR4 | Hack Tool / Exploit Match | HIGH | YARA rule match for hack tools or exploit code |

### MCP Least Privilege (4 patterns)

| ID | Pattern | Severity | Description |
|----|---------|----------|-------------|
| LP1 | Underdeclared Capability | HIGH | Code uses capabilities not listed in declared permissions |
| LP2 | Wildcard Permission | MEDIUM | Permission list contains wildcards (\*, all, full, any) |
| LP3 | Missing Permission Declaration | MEDIUM | No permissions field but code has detectable capabilities |
| LP4 | Overdeclared Permission | LOW | Permission declared but no corresponding code capability found |

### MCP Tool Poisoning (4 patterns)

| ID | Pattern | Severity | Description |
|----|---------|----------|-------------|
| TP1 | Hidden Instructions | HIGH | Hidden directives in metadata (HTML comments, zero-width chars, base64, data URIs) |
| TP2 | Unicode Deception | HIGH | Homoglyphs, RTL overrides, mixed-script identifiers in tool metadata |
| TP3 | Parameter Description Injection | MEDIUM | Injection patterns in parameter definitions (overrides, system tokens, malicious defaults) |
| TP4 | Description-Behavior Mismatch | MEDIUM | Declared tool description does not match actual code behavior (LLM-powered) |

All detected patterns are listed in the tables above.

## Risk Scoring

### Score Calculation

- **CRITICAL issues**: +50 points
- **HIGH issues**: +25 points
- **MEDIUM issues**: +10 points
- **LOW issues**: +5 points
- **Executable scripts**: 1.3x multiplier

### Severity Levels

| Score | Severity | Recommendation |
|-------|----------|----------------|
| 0-20 | LOW | SAFE |
| 21-50 | MEDIUM | CAUTION |
| 51-80 | HIGH | DO NOT INSTALL |
| 81-100 | CRITICAL | DO NOT INSTALL |

## Example Output

### Terminal Output

```
 SkillSpector Security Report  v2.0.0

Skill: suspicious-skill
Source: ./suspicious-skill/
Scanned: 2026-01-29 10:30:00 UTC

        Risk Assessment
 Metric          Value
 Score           78/100
 Severity        HIGH
 Recommendation  DO NOT INSTALL

        Components (3)
 File              Type      Lines  Executable
 SKILL.md          markdown    142  No
 scripts/sync.py   python       87  Yes
 requirements.txt  text          3  No

Issues (2)

  HIGH: Env Variable Harvesting (E2)
    Location: scripts/sync.py:23
    Finding: for key, val in os.environ.items():...
    Confidence: 94%
    Explanation: This code collects environment variables containing
    API keys and secrets, then sends them to an external server.

  HIGH: External Transmission (E1)
    Location: scripts/sync.py:45
    Finding: requests.post("https://api.skill.io/env"...
    Confidence: 89%
    Explanation: Data is being sent to an external server. Combined
    with env harvesting above, this indicates credential exfiltration.
```

## Configuration

### Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `SKILLSPECTOR_PROVIDER` | Active LLM provider. Choose `openai` (default) or `openai_compatible`. | Optional |
| `OPENAI_API_KEY` | Credential for `openai`. Also used when `openai_compatible` has incomplete credentials. That fallback defaults to `gpt-5.4` for slots without a model override. | Required for the OpenAI route |
| `OPENAI_BASE_URL` | Override the OpenAI endpoint (e.g. point at Ollama). | Optional |
| `SKILLSPECTOR_REASONING_EFFORT` | Optional reasoning effort passed to the endpoint. Unset or blank preserves the endpoint default. `gpt-6.1-sol` accepts only `low`, `medium`, `high`, `xhigh`, or `max`. Unsupported values for that model stop the scan before analysis (exit code 2). | Optional |
| `SKILLSPECTOR_OUTPUT_LANGUAGE` | Short, single-line language label (letters, numbers, spaces, `_`, or `-`; maximum 64 characters) for human-readable LLM finding text such as messages, explanations, and remediation. Rule IDs, severity values, paths, code, and other machine-readable values remain unchanged. Unset, blank, or invalid values preserve the default output language. | Optional |
| `SKILLSPECTOR_TEMPERATURE` | Optional sampling temperature from `0` to `1`. Unset or blank preserves the endpoint default. `gpt-6.1-sol` and Claude models from the Opus 4.7+, Sonnet 5, Fable 5, Mythos 5, and Haiku 5 families reject explicit temperature on these two provider routes. Static scans skip provider initialization and control validation. | Optional |
| `SKILLSPECTOR_SEED` | Optional signed 64-bit integer sampling seed. Endpoint and model support is best-effort. | Optional |
| `SKILLSPECTOR_COMPACT_PROMPTS` | Opt-in compact line numbering in LLM prompts: numbered lines render as `L1:`, `L2:` instead of zero-padded `L01:`, `L02:`. Accepted truthy values are `1`, `true`, and `yes` (case-insensitive; surrounding whitespace is trimmed). Unset or any other value keeps the default zero-padded format. | Optional |
| `SKILLSPECTOR_COMPAT_API_KEY` | API key for a generic OpenAI-compatible provider. | Required when `SKILLSPECTOR_PROVIDER=openai_compatible` |
| `SKILLSPECTOR_COMPAT_BASE_URL` | Base URL for a generic OpenAI-compatible provider. | Required when `SKILLSPECTOR_PROVIDER=openai_compatible` |
| `SKILLSPECTOR_COMPAT_HEADERS` | Optional JSON object with string keys and values for compatible gateway requests. | Optional |
| `SKILLSPECTOR_MODEL` | Override the active provider's bundled default model. | Optional |
| `SKILLSPECTOR_MODEL_<SLOT>` | Override a single analyzer slot, for example with `SKILLSPECTOR_MODEL_META_ANALYZER`. This takes precedence over `SKILLSPECTOR_MODEL`. See [model provenance](docs/INFERENCE_USAGE.md#model-provenance). | Optional |
| `SKILLSPECTOR_MODEL_REGISTRY` | Override the bundled per-provider YAML registry (`src/skillspector/providers/<provider>/model_registry.yaml`) with a custom path. | Optional |
| `SKILLSPECTOR_LOG_LEVEL` | Log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` (default: `WARNING`). | Optional |
| `SKILLSPECTOR_MAX_WORKFLOW_SECONDS` | Aggregate scan deadline in seconds; defaults to `1080` (18 minutes). Accepts a positive finite number. Invalid values warn and retain the default. Set before starting the process; caller-supplied budgets and external tool timeouts still apply. | Optional |
| `SKILLSPECTOR_TP4_MAX_BATCHES` | Maximum description-behavior review batches per TP4 analyzer execution; defaults to `256`. Accepts an integer from `1` to `4096`. Unset or blank uses the default; invalid values warn and retain it. Set before starting the process; file, byte, finding, concurrency, and workflow-time limits still apply. | Optional |
| `SKILLSPECTOR_MAX_DEPENDENCY_SOURCE_ANALYSIS_SECONDS` | Ceiling for the dependency-source analysis pass, in seconds. Defaults to `5.0`, which is the historical value. Raise it on slow or heavily loaded machines so the same unchanged tree does not come back partially inspected, which would set `safe_to_install` to false and record `runtime_limit`. The remaining aggregate workflow time (`SKILLSPECTOR_MAX_WORKFLOW_SECONDS`, 1080 seconds by default) still caps the effective value; values above that remaining time have no effect. Invalid, zero, negative, infinite, and NaN values keep the 5-second default. The setting is resolved when the module is imported, so a new process is required after changing it. See [analysis resource bounds](docs/ANALYSIS_RESOURCE_BOUNDS.md#configuring-the-dependency-source-deadline). | Optional |

### CLI Options

```bash
skillspector scan --help

Options:
  -f, --format [terminal|json|markdown|sarif]  Output format [default: terminal]
  -o, --output PATH                            Output file path
  --no-llm                                     Skip LLM analysis (static only)
  --yara-rules-dir PATH                        Extra YARA rules directory (includes disabled)
  -b, --baseline PATH                          Suppress findings listed in a baseline
  --show-suppressed                            List baseline-suppressed findings
  -V, --verbose                                Show detailed progress
  --help                                       Show this message and exit

# Generate a baseline of all current findings (see docs/SUPPRESSION.md)
skillspector baseline <path> [-o FILE] [--no-llm] [--reason TEXT]
```

Report files and new baselines use private permissions (0600). Existing baselines
retain their access metadata when safely replaced; updates requiring in-place
writes are refused. All file output uses atomic replacement. The parent directory
must be writable; symlinked
parents and non-regular destinations such as devices are refused. On macOS,
ancestor directories must also be readable. File output requires POSIX no-follow
directory operations and is unavailable on Windows. Unsupported file output fails
before analysis. Omit `--output` to send scan reports to stdout, or use
`skillspector baseline <path> -o -` for JSON on stdout, then redirect only to a
trusted destination.

## Integrating SkillSpector

SkillSpector is built to be driven by other tools (CI pipelines, install gates, editor integrations). Its exit code and JSON output are a stable contract.

### Exit codes

`skillspector scan` exits with:

| Code | Meaning |
|------|---------|
| `0` | Scan completed, `risk_score` ≤ 50 (recommendation `SAFE` or `CAUTION`), and no enabled strict gate fired |
| `1` | Scan completed and either `risk_score` > 50, `--fail-on-findings` found an active finding, `--fail-on-incomplete` found partial/incomplete analysis, or `--min-coverage` found coverage below its threshold |
| `2` | Error (bad input, unreadable source, internal failure) |

> By default, the exit code collapses `SAFE` and `CAUTION` into `0`. Use `--fail-on-findings` to gate on any active finding, `--fail-on-incomplete` to gate on incomplete coverage, `--min-coverage PERCENT` to gate on a coverage floor, or read the JSON `recommendation` field for custom policy.

### Machine-readable output

`--format json` produces a JSON report; with no `--output`/`-o` it is written to stdout:

```bash
skillspector scan ./my-skill/ --format json
```

The top-level shape is (this example shows a full LLM-backed scan; with `--no-llm`, `metadata.llm_requested` is `false`):

```json
{
  "skill": { "name": "...", "source": "...", "scanned_at": "<ISO 8601>" },
  "risk_assessment": { "score": 0, "severity": "LOW", "recommendation": "SAFE" },
  "components": [ { "path": "...", "type": "...", "lines": 0, "executable": false, "size_bytes": 0 } ],
  "issues": [ { "id": "...", "category": "...", "severity": "...", "confidence": 0.0, "location": { "file": "...", "start_line": 0 }, "surface": "code" } ],
  "metadata": {
    "has_executable_scripts": false,
    "skillspector_version": "...",
    "llm_requested": true,
    "llm_available": true,
    "inference_usage": [
      {
        "node": "semantic_security_discovery",
        "request_kind": "structured_output",
        "provider": "openai_compatible",
        "model": "azure/anthropic/claude-opus-4-6",
        "model_source": "provider_response",
        "usage_source": "provider_response",
        "prompt_tokens": 1000,
        "completion_tokens": 100,
        "cached_tokens": 400,
        "cache_write_tokens": 50,
        "total_tokens": 1100
      }
    ]
  }
}
```

- `risk_assessment.severity` ∈ `LOW | MEDIUM | HIGH | CRITICAL`.
- `risk_assessment.recommendation` ∈ `SAFE | CAUTION | DO_NOT_INSTALL`, mapped from severity: `LOW → SAFE`, `MEDIUM → CAUTION`, `HIGH`/`CRITICAL → DO_NOT_INSTALL`.
- `issues[].surface` and `suppressed[].surface` are advisory, heuristic labels for where a finding landed:
  `code | instructions | docs | tests | comments | config`. JSON expands compacted groups into one `issues[]` entry
  per occurrence, each with its own `surface`; SARIF emits `properties.surface` on each result. The label is
  path/line-based and the scanned author can influence it, so do not use it as the sole suppression or security signal.
- `metadata.llm_error` appears only when LLM analysis was requested but unavailable.
- Archive-member components retain their virtual `path` and include `outer_path`, `nested_path`,
  and container provenance. Virtual paths identify in-memory members, not on-disk files; see
  [JSON component locations](docs/NESTED_ARTIFACT_INSPECTION.md#json-component-locations).
- AE1 findings use **Incomplete referenced artifact analysis**. Their source
  location identifies the reference; `evidence` identifies the affected target,
  analyzer reasons, and available bounds. Review the target's completeness
  ledger when `reasons_truncated` is true. See
  [referenced-artifact diagnostics and Perl help text](docs/ANALYSIS_RESOURCE_BOUNDS.md#diagnosing-incomplete-referenced-artifacts)
  for interpretation and corrective actions.
- `metadata.inference_usage` contains one sanitized record per LLM response when the
  provider exposes token counters. It is an empty list when usage is unavailable;
  SkillSpector never estimates missing tokens. Prompt totals are inclusive of cache
  reads and writes so downstream pricing can separate those partitions safely.
  `model_source` distinguishes an independently identified provider model from
  the exact requested model used when response identity is absent or ambiguous.
  SkillSpector does not currently send Anthropic prompt-cache controls, so its
  scan requests cannot select the separate 5-minute or 1-hour cache-write tiers;
  TTL-specific response fields are normalized defensively into the aggregate
  cache-write counter.
- See [Inference usage telemetry](docs/INFERENCE_USAGE.md) for the complete
  provenance, cache-accounting, privacy, fail-closed ingestion, and downstream
  pricing contract.
- The full per-issue shape is defined by `Finding.to_dict()` in [models.py](src/skillspector/models.py); rely on the fields above and treat any additional fields as best-effort.

For CI/IDE tooling, `--format sarif` emits SARIF 2.1.0. Each result carries its
security severity; rule descriptors carry a score only when all of their results
share that severity. Mixed-severity rules remain unannotated, so consumers that
read only rule-level severity cannot distinguish their result severities.
Recursive scans chain each percent-encoded skill-directory URI through an
absolute scan-root file URI. External dependency locations retain their source
provenance instead of being rebased into the local skill directory.

### Recommended gate mapping

When using SkillSpector as an install gate, map the recommendation to an action:

| `recommendation` | Suggested action |
|------------------|------------------|
| `SAFE` | allow |
| `CAUTION` | prompt / warn the user |
| `DO_NOT_INSTALL` | block |

SkillSpector computes the score band and recommendation; how strict the gate is (e.g. whether `CAUTION` blocks in CI) is a policy decision for the integrating tool.

## Development

### Setup

This is the canonical setup recipe for contributing from this checkout. Use
**Python 3.12-3.15** (`>=3.12,<3.16`, as defined in [pyproject.toml](pyproject.toml)),
Git, and `make`. A release tag can have a different Python range; check that
tag's metadata. The commands below use Bash or Zsh.

With **uv** installed:

```bash
# From the SkillSpector checkout
uv venv --python 3.12 .venv
source .venv/bin/activate
make install-dev
```

If uv is unavailable, run these commands in the checkout with a supported
Python interpreter:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
make install-dev
```

Create and activate the virtual environment before running any `make` target.
`make install-dev` uses `uv sync --all-extras` when uv is available, otherwise
`pip install -e ".[dev]"`.

Start with these local checks:

```bash
make test-unit
make lint
make format-check
```

`make test-unit` excludes tests marked `integration` or `provider`. This selection
does not cover every CI job or guarantee network isolation.

Some unit tests start loopback HTTP servers and local multiprocessing sockets.
Restricted agent sandboxes can deny these operations. Keep the failing
command and traceback, and use an appropriately permitted environment for a
diagnostic rerun. Report that result separately; a passing subset does not make
the original full target pass. See the
[contributor onboarding check](docs/validation/CONTRIBUTOR_ONBOARDING.md).

Before submitting a PR, [CONTRIBUTING.md](CONTRIBUTING.md#how-to-contribute)
requires `make test`. It runs both `test-unit` and `test-integration`; integration
tests invoke the full graph and may send test content to a configured LLM provider.
Review the [provider configuration](docs/DEVELOPMENT.md#10-environment-and-configuration)
and credentials or local-service prerequisites before running it:

```bash
make test
```

`make test-provider` makes live provider requests and requires the corresponding
provider configuration; see [Live provider tests](docs/DEVELOPMENT.md#live-provider-tests).
`make test-cov` runs the same marker-filtered selection as `test-unit`, with coverage.

`make format` applies lint fixes and formats files **in place**. Run it before
committing when changes are needed, review the diff, then recheck with `make lint`
and `make format-check`.

## How It Works

SkillSpector uses a two-stage detection pipeline:

### Stage 1: Static Analysis
- Fast regex-based pattern matching across 11 static analyzers
- AST-based behavioral analysis detecting dangerous calls (exec, eval, subprocess, etc.)
- Live vulnerability lookups via OSV.dev for known CVEs in dependencies
- Scans all analyzer-eligible files in the skill
- High recall (catches most issues)
- Moderate precision (some false positives)

OpenSSF Model Signing / DSSE bundles remain in content analysis, regardless of
filename. Static and LLM analyzers inspect decoded JSON payloads, readable DER
certificate/signature content, and all unknown wrapper fields. Strictly parsed
binary fields are projected rather than treated as executable base64; canonical
bytes remain available to YARA and fingerprints. Unsupported encodings (including
environments without the optional `cryptography` dependency) retain raw fields
and produce an explicit incomplete-analysis warning. Findings in decoded content
point to the original bundle and label their projection in evidence. None of this
verifies signatures, certificate chains, transparency logs, or signer identity.

### Stage 2: LLM Semantic Analysis (Optional)
- Evaluates context and intent
- Confirmed findings may gain an explanation and higher confidence, never lower
- Every deterministic finding stays in the report whether the model confirms it, disputes it, or does not address it
- Findings the model reviews but does not confirm (disputed, low-confidence, or unaddressed) are tagged `llm-unconfirmed` in JSON and SARIF output
- Findings whose review fails are kept without that tag; `evidence.llm_review_outcome` distinguishes `confirmed`, `disagreed`, `low-confidence`, `missing`, and `failed` in JSON and SARIF output
- Results depend on the configured model and prompt context; Stage 2 does not replace
  deterministic findings or guarantee a particular precision rate

The LLM prompt includes anti-jailbreak protections to prevent malicious skills from manipulating the analysis.

## Live Vulnerability Lookups (SC4)

SC4 uses the [OSV.dev](https://osv.dev) API to check dependencies against the full Open Source Vulnerabilities database — covering tens of thousands of advisories across PyPI and npm.

- **No API key required** — OSV.dev is free and unauthenticated.
- **Batch queries** — all dependencies are checked in a single HTTP call.
- **Automatic fallback** — if OSV.dev is unreachable (air-gapped/offline), a small built-in fallback list is used.
- **Caching** — results are cached in-memory for 1 hour to avoid redundant API calls during a session.
- **Pipenv** — reads `Pipfile` package tables and the direct/transitive packages in
  `Pipfile.lock`, including development and custom categories. Exact lock versions
  can resolve unpinned manifest dependencies. Malformed entries and unsupported
  VCS/local sources are reported as incomplete dependency checks.

The tool requires outbound HTTPS access to `api.osv.dev` for live vulnerability data. When that is not available, findings are limited to the static fallback list.

## Locked npm dependency contents (opt-in)

Use `--inspect-dependencies` to statically inspect exact public npm tarballs from
`package-lock.json` or `npm-shrinkwrap.json` v2/v3, including recorded transitive,
development, and optional dependencies:

```bash
skillspector scan ./my-skill --inspect-dependencies --no-llm --fail-on-incomplete
```

Artifacts must match their locked SHA-512 integrity hashes. A disposable Linux
worker inspects supported source files and decoded npm scripts without installing
packages or executing their code. Dependency findings include package coordinates,
an artifact digest, and source locations. Missing artifacts, unsupported sources
or formats, unavailable isolation, and exhausted budgets remain incomplete;
`--fail-on-incomplete` prevents them from passing the scan. Reports also show when
inspection was not requested.

This first version supports the public npm registry and Linux x86-64/AArch64
seccomp isolation. Dependency analysis stays local and uses existing static
data-exfiltration, privilege-escalation, and supply-chain patterns. See
[supported content, resource limits, and coverage boundaries](docs/DEPENDENCY_CONTENT_INSPECTION.md).

## Trust model and data egress

SkillSpector is defense-in-depth, not a sandbox. Know what it does and does not do before relying on it:

- **It never executes the scanned skill.** All analysis is static (regex, Python AST, YARA) plus optional LLM evaluation of file *contents* — the skill's code is never run.
- **LLM analysis sends analyzer-eligible file contents to the configured provider.** When LLM analysis is enabled (the default), file contents are sent to the active `SKILLSPECTOR_PROVIDER` endpoint. DSSE payloads and readable signing metadata are included. Use `--no-llm` to keep contents local (static analysis only).
- **SC4 sends dependency names to OSV.dev.** The supply-chain check queries [OSV.dev](https://osv.dev) with the package names and versions the skill declares, to look up known CVEs. This is fundamental to the check and runs even with `--no-llm`. It sends dependency coordinates (not file contents), requires no API key, and falls back to a bundled list when OSV.dev is unreachable.
- **It does not sandbox the host.** SkillSpector flags risky patterns *before* you install a skill; it does not contain or isolate a skill you choose to install anyway.

## Limitations

- **Non-English content**: May miss patterns in other languages
- **Image-based attacks**: Cannot analyze text in images
- **Encrypted/binary code**: Cannot analyze compiled or encrypted content
- **Runtime behavior**: Static analysis only, no dynamic execution
- **Offline SC4**: Without network access to `api.osv.dev`, SC4 uses a small static fallback list

## Research Background

Based on research from "Agent Skills in the Wild: An Empirical Study of Security Vulnerabilities at Scale" (Liu et al., 2026):

- **Dataset**: 42,447 skills from major marketplaces; 31,132 were analyzed for the following rates
- **Vulnerable**: 26.1% of the analyzed subset contain at least one vulnerability
- **High-severity**: 5.2% of the analyzed subset show likely malicious intent
- **Key finding**: Skills with executable scripts are 2.12x more likely to be vulnerable

## Python API Integration

```python
from skillspector import graph
from skillspector.cleanup import cleanup_result

# Invoke the LangGraph workflow
result = graph.invoke({
    "input_path": "/path/to/skill",
    "output_format": "json",   # terminal, json, markdown, or sarif
    "use_llm": True,           # False for static-only analysis
})

try:
    print(f"Risk Score: {result['risk_score']}/100")
    print(f"Severity: {result['risk_severity']}")
    print(f"Recommendation: {result['risk_recommendation']}")

    for finding in result["filtered_findings"]:
        print(f"[{finding.severity}] {finding.rule_id}: {finding.message}")
finally:
    cleanup_result(result)
```

## License

Apache License 2.0 - see [LICENSE](LICENSE) for details.

## Contributing

Report suspected security vulnerabilities, including vulnerabilities in SkillSpector
itself, privately to NVIDIA PSIRT by following [SECURITY.md](SECURITY.md). Do not
report them through GitHub issues, pull requests, or Discussions.

Contributions are welcome! Please read our contributing guidelines and submit pull requests.

## Support

- **Security vulnerabilities**: Follow [SECURITY.md](SECURITY.md) to report privately
  to NVIDIA PSIRT. Do not report them through GitHub issues, pull requests, or Discussions.
- **Issues**: [GitHub Issues](https://github.com/NVIDIA/skillspector/issues) for
  non-security bugs, false positives, and feature requests. Use minimal, sanitized reproductions.
