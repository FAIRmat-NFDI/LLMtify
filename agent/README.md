# hackframe

Config-driven, object-oriented agentic framework on **Microsoft Agent Framework (Python, ≥1.22)**.
Scientists add skills, MCP servers, context providers, tools and executors; the framework wires
them into agents and workflows with per-agent model choice, structured I/O, OTel → Aspire and DevUI.

## Layout

```
agentic-hackframe/
├── pyproject.toml
├── .env.example
├── docker-compose.yml            # Aspire dashboard (OTLP) + LiteLLM proxy
├── config/
│   ├── models.yaml               # model profiles: mini -> gpt-5-mini, nano -> gpt-5-nano
│   ├── agents.yaml               # exA (mini), exB (nano): instructions, ctx, tools, mcp, skills
│   ├── mcp.yaml                  # stdio (local/internal) + http (external) MCP servers
│   ├── workflows.yaml            # agents_fanout (patterns: sequential | fan_out_in | graph)
│   ├── litellm.config.yaml       # alias -> provider routing for the proxy
│   └── knowledge/domain_notes.md # scientist-editable context, injected by `domain_notes`
├── skills/
│   └── hello-skill/              # SKILL.md + references/  (Agent Skills format)
├── src/hackframe/
│   ├── main.py                   # CLI: list | run | agent | devui
│   ├── settings.py               # env / .env settings
│   ├── core/
│   │   ├── registry.py           # generic plugin Registry (one per extension point)
│   │   ├── config.py             # typed YAML loading + ${VAR:-default}
│   │   ├── schemas.py            # TaskInput, StepMessage, StepResult, AgentAnswer, FinalOutput
│   │   └── runtime.py            # composition root: factories, agents, workflows, lifecycle
│   ├── llm/providers.py          # ModelProvider ABC, LiteLLM proxy + OpenAI providers, ModelFactory
│   ├── agents/factory.py         # AgentSpec -> Agent (single agent type)
│   ├── context/providers.py      # BaseHackContextProvider, session_info, domain_notes, RAG stub
│   ├── skills/loader.py          # skills/ -> SkillsProvider
│   ├── mcp/
│   │   ├── factory.py            # MCPStdioTool / MCPStreamableHTTPTool from config
│   │   └── servers/hello_server.py  # internal demo MCP server (FastMCP)
│   ├── tools/builtin.py          # local @tool functions (utc_now, unit_convert)
│   ├── executors/
│   │   ├── base.py               # BaseStepExecutor (structured I/O, timing, error capture)
│   │   ├── builtin.py            # Intake, Merge (fan-in), Finalize
│   │   ├── hello.py              # placeholder "hello world" executor
│   │   └── llm.py                # AgentStepExecutor (structured output via response_format)
│   ├── workflows/builder.py      # WorkflowSpec -> Workflow
│   ├── telemetry/otel.py         # configure_otel_providers -> Aspire
│   └── devui/server.py           # DevUI with every agent + workflow
└── tests/test_smoke.py           # offline: workflow + agent wiring
```

## Data flow (structured I/O)

```
TaskInput ─▶ intake ─▶ StepMessage ─▶ [steps: sequential | fan-out/in | graph] ─▶ finalize ─▶ FinalOutput
```
Every step consumes a `StepMessage` and forwards it with one `StepResult` appended, so any executor
fits in any topology. LLM steps request `AgentAnswer` via `response_format`.

## Run

```bash
cp .env.example .env            # add OPENAI_API_KEY (or other provider keys)
pip install -e ".[dev]"
docker compose up -d            # Aspire UI: http://localhost:18888 · LiteLLM: :4000

python -m hackframe list                                   # config + registered plugins
python -m hackframe run agents_fanout "explain entropy"    # exA (gpt-5-mini) ‖ exB (gpt-5-nano)
python -m hackframe agent exA "what is entropy?"
python -m hackframe devui                                  # http://localhost:8080
pytest -q
```

## Extend

| Want | Do |
|---|---|
| New model | add alias in `litellm.config.yaml`, profile in `models.yaml`, set `model:` on the agent |
| New agent | add a block in `agents.yaml` |
| New step type | subclass `BaseStepExecutor`, implement `process()`, `@EXECUTORS.register("key")`, import it in `executors/__init__.py` |
| New workflow | add to `workflows.yaml` (`sequential` / `fan_out_in` / `graph`) |
| New skill | `skills/<name>/SKILL.md` (+ `references/`, `scripts/`); agent `skills: true` |
| New MCP server | entry in `mcp.yaml` (stdio or http); list key under agent `mcp:` |
| New context provider | subclass `BaseHackContextProvider`, `@CONTEXT_PROVIDERS.register("key")` |
| New local tool | `@register_tool("key")` in `tools/builtin.py` |
| New model backend | subclass `ModelProvider`, `@PROVIDERS.register("key")` |

Notes: skill script execution keeps approval on (load/read are auto-approved). MCP connections are
opened lazily per agent and closed by `Runtime.open_workflow`. DevUI auth is off by default for local
use (`DEVUI_AUTH_ENABLED`).
