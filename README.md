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

## Example: executor + skill + tools (molecule report)

Goal: SMILES in → PubChem lookup → Lipinski check → structured report.

```
TaskInput(smiles) ─▶ chem_report (agent: chemist)
                       ├─ skill:  drug-likeness   (tells the agent the procedure)
                       ├─ tool:   pubchem_lookup  (existing)
                       └─ tool:   lipinski_check  (new)
                     ─▶ MoleculeReport (structured)
```

**1. Tool** – `src/hackframe/tools/chem.py`
```python
from typing import Annotated

from hackframe.tools.builtin import register_tool


@register_tool("lipinski_check")
def lipinski_check(
    molecular_weight: Annotated[float, "Molecular weight in g/mol"],
    xlogp: Annotated[float, "XLogP (lipophilicity)"],
    h_bond_donors: Annotated[int, "Number of H-bond donors"],
    h_bond_acceptors: Annotated[int, "Number of H-bond acceptors"],
) -> dict:
    """Lipinski rule of five: drug-like if at most one rule is violated."""
    violations = [
        name
        for name, failed in {
            "MW > 500": molecular_weight > 500,
            "XLogP > 5": xlogp > 5,
            "HBD > 5": h_bond_donors > 5,
            "HBA > 10": h_bond_acceptors > 10,
        }.items()
        if failed
    ]
    return {"violations": violations, "drug_like": len(violations) <= 1}
```
Register it in `src/hackframe/tools/__init__.py`:
```python
from hackframe.tools import builtin, chem, pubchem  # noqa: F401
```

**2. Skill** – `skills/drug-likeness/SKILL.md`
```markdown
---
name: drug-likeness
description: Use when asked whether a molecule (given as SMILES) is drug-like or passes Lipinski's rule of five.
---

# Drug-likeness assessment

1. Call `pubchem_lookup` with the SMILES (set include_synonyms=true).
2. If `found` is false, stop and report the error.
3. Call `lipinski_check` with molecular_weight, xlogp, h_bond_donors, h_bond_acceptors from step 1.
4. Report: common name, CID, formula, MW, the violations list and a one-line verdict.
Never guess property values; only use numbers returned by the tools.
```

**3. Agent** – append to `config/agents.yaml`
```yaml
  chemist:
    description: Molecule profiler (PubChem + Lipinski)
    model: hu
    instructions: |
      You are a cheminformatics assistant. Use tools for every number you report.
    context_providers: [session_info]
    tools: [pubchem_lookup, lipinski_check]
    skills: true
```

**4. Executor** – `src/hackframe/executors/chem_report.py`
```python
from pydantic import BaseModel, Field

from hackframe.core.schemas import StepMessage
from hackframe.executors.base import EXECUTORS
from hackframe.executors.llm import AgentStepExecutor


class MoleculeReport(BaseModel):
    name: str | None = Field(None, description="Common name")
    cid: int | None = None
    formula: str | None = None
    molecular_weight: float | None = None
    lipinski_violations: list[str] = Field(default_factory=list)
    drug_like: bool | None = None
    verdict: str = Field(..., description="One-sentence conclusion")


@EXECUTORS.register("chem_report")
class ChemReportExecutor(AgentStepExecutor):
    executor_type = "chem_report"
    response_format = MoleculeReport  # structured output schema for this step

    def build_prompt(self, message: StepMessage) -> str:
        smiles = message.task.params.get("smiles", message.task.task)
        return f"Use the drug-likeness skill. Assess this molecule: {smiles}"
```
Register it in `src/hackframe/executors/__init__.py`:
```python
from hackframe.executors import builtin, chem_report, llm, preprocess  # noqa: F401
```

**5. Workflow** – append to `config/workflows.yaml`
```yaml
  molecule_report:
    description: SMILES -> PubChem lookup -> Lipinski check -> structured report
    pattern: sequential
    steps:
      - {id: report, type: chem_report, agent: chemist}
```

**6. Run**
```bash
python -m hackframe list        # shows chem_report, lipinski_check, drug-likeness
python -m hackframe run molecule_report "aspirin" --params '{"smiles": "CC(=O)OC1=CC=CC=C1C(=O)O"}'
python -m hackframe devui       # or pick molecule_report in DevUI
```
DevUI input: `{"task": "aspirin", "params": {"smiles": "CC(=O)OC1=CC=CC=C1C(=O)O"}}`

Who owns what: the **tool** does the computation, the **skill** holds the procedure (editable by
scientists without code), the **agent** bundles model + tools + skills, the **executor** fixes the
prompt and output schema for one workflow step.

## Material pipeline (scraper + judge)

Input: one material JSON (`systems` + `cifs`). Output: a natural-language summary.

```
JSON ─▶ preprocess ─▶ compact ─▶ tool_finder ══(switch-case on WorkItem.next_tool)══▶ cif_to_smiles ─▶ pubchem ─▶ pubchem_safety ══▶ scrape ─▶ judge ─▶ summary
         temp/<uuid>.json   small view   LLM + skill tool-finder                         ASE+RDKit    LLM + PubChem MCP   PUG-View      gather     LLM + skill judge
```

| Executor | File | Role |
|---|---|---|
| `preprocess` | `executors/preprocess.py` | save full JSON (incl. CIFs) to `temp/<uuid>.json` (MAF FileSystemAgentFileStore) |
| `compact` | `executors/compact.py` | small view without CIF bodies |
| `tool_finder` | `executors/tool_finder.py` | **agentic switch**: LLM grounded on `skills/tool-finder` plans a tool chain per subsystem |
| `cif_to_smiles` | `executors/tools_chem.py` | tool: CIF → SMILES via `tools/descriptors.py` (ASE + RDKit) |
| `pubchem` | `executors/tools_chem.py` (`pubchem_mcp`) | tool, agentic: LLM + **BioContext PubChem MCP** (skill `pubchem-scrape`) → CID, name, formula, synonyms |
| `pubchem_safety` | `executors/tools_chem.py` | tool: CID → GHS pictograms, industry uses, vendor count (PUG-View; not offered by the MCP server) |
| `scrape` | `executors/routing.py` (auto) | gathers all subsystem findings (MAF workflow state) |
| `judge` | `executors/judge.py` | LLM grounded on `skills/judge` writes the summary from the evidence |

Routing is MAF-native: the router sets `WorkItem.plan`, and `add_switch_case_edge_group` sends each item to the
tool named by `next_tool` (or to `scrape` when the chain is done). LLM calls per run: tool_finder 1, judge 1,
pubchem about 2–4 per molecule subsystem (tool-calling loop).

**Domain experts edit skills, not code:**
- `skills/tool-finder/SKILL.md`: which subsystems to process and with which tools. `metadata:` holds the fallback
  rule used if the LLM is unreachable (`fallback_structural_types`, `fallback_tools`).
- `skills/judge/SKILL.md`: what the summary says and in which order.

**Add a tool** (e.g. `element_lookup`):
1. `class ElementLookup(BaseToolExecutor)` with `description` and `async def run_tool(self, item) -> dict`;
   read earlier outputs with `item.find("smiles")`; `@EXECUTORS.register("element_lookup")`.
2. `config/workflows.yaml`: add the step and list it under `tools:`.
3. `skills/tool-finder/SKILL.md`: add the rule for when to use it.

Results carry `generated_by: "llm" | "fallback"` (judge) and `scrape.planner.by: "llm" | "fallback"` (tool_finder).

```bash
python -m hackframe run material_pipeline --input-file examples/material_example.json
```

### PubChem MCP server

- [BioContext/PubChem-MCP](https://github.com/BioContext/PubChem-MCP), installed by `pip install -e .` (pinned commit, not on PyPI),
  started by MAF as a stdio process (`config/mcp.yaml` → `pubchem`); the `pubchem` agent has `mcp: [pubchem]`.
- Exposed tools: `search_compound` (by **name only**), `get_compound_details`, `get_compound_properties`
  (`search_bioassay`, `get_substance_details` exist but are filtered out with `allowed_tools`).
- Because there is no SMILES search, the agent names the fragment from `heavy_fragments` (SMILES without H)
  and verifies the hit's formula. Rules: `skills/pubchem-scrape/SKILL.md`.
- Requires `mcp<2` (the server uses `FastMCP`, removed in MCP SDK 2.x).
- Needs a model with tool calling (check HU vLLM). The answer is a JSON block, not `response_format`,
  so tool calls are not blocked by constrained decoding.
- Proxy/CA env vars are passed through to the MCP process (`mcp/factory.py`).
