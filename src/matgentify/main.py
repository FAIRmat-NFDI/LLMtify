"""CLI.

  python -m matgentify list
  python -m matgentify run material_pipeline --input-file examples/material_example.json
  python -m matgentify agent exA "what is entropy?"
  python -m matgentify devui
  python -m matgentify pubchem "CCO"
"""

import argparse
import asyncio
import json
import logging
from pathlib import Path

from dotenv import load_dotenv

from matgentify.core.runtime import Runtime
from matgentify.core.schemas import AgentAnswer, TaskInput
from matgentify.telemetry.otel import setup_telemetry


async def _run_workflow(
    rt: Runtime, name: str, task: str | None, params: str, input_json: str | None, input_file: str | None
) -> None:
    if input_file:  # whole JSON from a file, e.g. examples/material_example.json
        payload: object = json.loads(Path(input_file).read_text())
    elif input_json:  # workflow-specific input as inline JSON
        payload = json.loads(input_json)
    elif task is not None:
        payload = TaskInput(task=task, params=json.loads(params))
    else:
        raise SystemExit("give a task or --input '<json>'")
    out = await rt.run_workflow(name, payload)
    if isinstance(out, str):
        print(out)  # e.g. a markdown report
    else:
        print(out.model_dump_json(indent=2) if hasattr(out, "model_dump_json") else json.dumps(out, indent=2))


async def _run_agent(rt: Runtime, name: str, prompt: str) -> None:
    async with rt.agent(name) as agent:
        resp = await agent.run(prompt, options={"response_format": AgentAnswer})
        print(resp.value.model_dump_json(indent=2) if resp.value else resp.text)


def _export_graph(rt: Runtime, name: str, fmt: str, output: str | None, internal: bool) -> None:
    """MAF built-in visualizer: WorkflowViz(workflow).to_mermaid() / to_digraph() / export(format=...)."""
    from pathlib import Path

    from agent_framework import WorkflowViz

    viz = WorkflowViz(rt.workflow(name))
    if fmt in ("mermaid", "dot"):
        text = viz.to_mermaid(include_internal_executors=internal) if fmt == "mermaid" \
            else viz.to_digraph(include_internal_executors=internal)
        if output:
            Path(output).write_text(text)
            print(f"wrote {output}")
        else:
            print(text)
        return
    out = output or f"docs/{name}.{fmt}"
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    print("wrote", viz.export(format=fmt, filename=out, include_internal_executors=internal))


def cli() -> None:
    load_dotenv()
    p = argparse.ArgumentParser(prog="matgentify")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--no-telemetry", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="show config + registered plugins")

    r = sub.add_parser("run", help="run a workflow")
    r.add_argument("workflow")
    r.add_argument("task", nargs="?")
    r.add_argument("--params", default="{}", help="JSON object (with task)")
    r.add_argument("--input", help="workflow input as inline JSON")
    r.add_argument("--input-file", help="workflow input from a JSON file")

    a = sub.add_parser("agent", help="run a single agent")
    a.add_argument("name")
    a.add_argument("prompt")

    d = sub.add_parser("devui", help="launch DevUI")
    d.add_argument("--port", type=int)

    g = sub.add_parser("graph", help="export a workflow graph with MAF WorkflowViz")
    g.add_argument("workflow")
    g.add_argument("--format", default="mermaid", choices=["mermaid", "dot", "svg", "png", "pdf"],
                   help="mermaid/dot print text; svg/png/pdf need graphviz (pip install graphviz + apt install graphviz)")
    g.add_argument("-o", "--output", help="output file (default: docs/<workflow>.<ext>)")
    g.add_argument("--internal", action="store_true", help="include internal executors (intake/finalize)")

    pc = sub.add_parser("pubchem", help="test the PubChem tool directly (no LLM)")
    pc.add_argument("smiles")

    args = p.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    if args.cmd == "pubchem":
        from matgentify.tools.pubchem import cli_lookup

        asyncio.run(cli_lookup(args.smiles))
        return

    rt = Runtime()

    if args.cmd == "list":
        print(json.dumps(rt.describe(), indent=2))
        return
    if args.cmd == "graph":
        _export_graph(rt, args.workflow, args.format, args.output, args.internal)
        return
    if args.cmd == "devui":
        from matgentify.devui.server import launch

        launch(rt, port=args.port)
        return

    if not args.no_telemetry:
        setup_telemetry(rt.settings)
    if args.cmd == "run":
        asyncio.run(_run_workflow(rt, args.workflow, args.task, args.params, args.input, args.input_file))
    elif args.cmd == "agent":
        asyncio.run(_run_agent(rt, args.name, args.prompt))


if __name__ == "__main__":
    cli()
