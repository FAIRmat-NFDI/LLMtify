# hackframe – Quick Guide

## 1. Enable the LLM (OpenAI → LiteLLM → agents)

```
agent (exA) ──▶ LiteLLM :4000 (alias gpt-5-mini) ──▶ OpenAI (openai/gpt-5-mini)
```

**a) Key** – in `.env`:
```bash
OPENAI_API_KEY=sk-...
LITELLM_BASE_URL=http://localhost:4000/v1
LITELLM_API_KEY=sk-hackathon-master-key
```

**b) Alias → provider** – `config/litellm.config.yaml`:
```yaml
model_list:
  - model_name: gpt-5-mini              # alias the app uses
    litellm_params:
      model: openai/gpt-5-mini          # real provider/model
      api_key: os.environ/OPENAI_API_KEY
```

**c) Profile → alias** – `config/models.yaml`:
```yaml
profiles:
  mini: {provider: litellm, model: gpt-5-mini}
  nano: {provider: litellm, model: gpt-5-nano}
```

**d) Agent → profile** – `config/agents.yaml`:
```yaml
agents:
  exA:
    model: mini        # this agent uses gpt-5-mini
```

**e) Start + check**
```bash
docker compose up -d
curl -s localhost:4000/v1/models -H "Authorization: Bearer sk-hackathon-master-key"
python -m hackframe agent exA "hello"
```

New model = add alias (b) + profile (c) + set `model:` on the agent (d). Restart LiteLLM after editing (b).

---

## 2. Create a new executor

**a) Code** – `src/hackframe/executors/my_steps.py`:
```python
from typing import Any
from hackframe.core.schemas import StepMessage
from hackframe.executors.base import EXECUTORS, BaseStepExecutor

@EXECUTORS.register("normalize")
class NormalizeExecutor(BaseStepExecutor):
    executor_type = "normalize"

    async def process(self, message: StepMessage) -> dict[str, Any]:
        unit = self.params.get("unit", "eV")            # from workflows.yaml
        previous = [r.output for r in message.results]  # upstream outputs
        return {"unit": unit, "task": message.task.task.strip().lower(), "seen": len(previous)}
```

**b) Register** – add it to the import in `src/hackframe/executors/__init__.py`:
```python
from hackframe.executors import builtin, llm, my_steps, preprocess  # noqa: F401
```

**c) Use** – in `config/workflows.yaml`:
```yaml
- {id: prep, type: normalize, unit: meV}   # extra keys -> self.params
```

Rules: return a `dict`; raising an exception marks the step `error` and the workflow continues.
LLM step = `type: agent` + `agent: <name>` (no code needed).

---

## 3. Add tools and skills

Tools and skills attach to **agents**; an executor gets them via `type: agent, agent: <name>`.

**Local tool** – `src/hackframe/tools/builtin.py`:
```python
@register_tool("molar_mass")
def molar_mass(formula: Annotated[str, "Chemical formula"]) -> float:
    """Return molar mass in g/mol."""
    ...
```

**MCP server** – `config/mcp.yaml`:
```yaml
mcp_servers:
  my_local:                       # internal, stdio
    transport: stdio
    command: python
    args: ["-m", "hackframe.mcp.servers.my_server"]
  my_remote:                      # external, HTTP
    transport: http
    url: https://example.org/mcp
```
(copy `src/hackframe/mcp/servers/hello_server.py` as a template for `my_server.py`)

**Skill** – folder `skills/<name>/SKILL.md`:
```markdown
---
name: xrd-analysis
description: Use when the user asks to interpret XRD peaks.
---
Steps: ...
```

**Wire to an agent** – `config/agents.yaml`:
```yaml
agents:
  exA:
    model: mini
    tools: [utc_now, molar_mass]
    mcp: [hello_local, my_local]
    skills: true                 # all folders in skills/
```

---

## 4. Chaining (`config/workflows.yaml`)

Every workflow runs `intake -> <your steps> -> finalize`. Steps pass a `StepMessage` that
accumulates each step's result, so any executor can follow any other.

**Sequential**
```yaml
my_seq:
  pattern: sequential
  steps:
    - {id: prep,    type: normalize}
    - {id: analyst, type: agent, agent: exA}
    - {id: critic,  type: agent, agent: exB}
```
`prep → analyst → critic`

**Fan-out / fan-in** (parallel, results merged)
```yaml
my_par:
  pattern: fan_out_in
  steps:
    - {id: analyst, type: agent, agent: exA}
    - {id: critic,  type: agent, agent: exB}
```
`intake ⇒ {analyst, critic} ⇒ merge → finalize`

**Graph** (mix both; one entry, fan-out, fan-in, one exit)
```yaml
my_graph:
  pattern: graph
  steps:
    - {id: prep,    type: normalize}
    - {id: analyst, type: agent, agent: exA}
    - {id: critic,  type: agent, agent: exB}
    - {id: report,  type: agent, agent: exA}
  entry: [prep]                          # first step(s); 2+ = fan-out from intake
  edges:
    - {from: prep, to: [analyst, critic]}   # fan-out
    - {from: [analyst, critic], to: report} # fan-in (auto-inserted merge)
  exit: report                           # step that feeds finalize
```
`prep ⇒ {analyst, critic} ⇒ report → finalize`

Edge forms: `a → b` (one-to-one), `a → [b, c]` (fan-out), `[b, c] → d` (fan-in).
Many-to-many is not supported; split it into a fan-in then a fan-out.

**Run**
```bash
python -m hackframe list                         # see workflows + registered executors
python -m hackframe run my_graph "your task" --params '{"k": "v"}'
python -m hackframe devui                        # all workflows appear in DevUI
```
