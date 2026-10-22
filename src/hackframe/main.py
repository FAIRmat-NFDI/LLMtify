"""CLI.

  python -m hackframe list
  python -m hackframe run material_pipeline --input-file examples/material_example.json
  python -m hackframe agent exA "what is entropy?"
  python -m hackframe devui
  python -m hackframe pubchem "CCO"
"""

import argparse
import asyncio
import json
import logging
from pathlib import Path

from dotenv import load_dotenv

from hackframe.core.runtime import Runtime
from hackframe.core.schemas import AgentAnswer, TaskInput
from hackframe.telemetry.otel import setup_telemetry


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


def cli() -> None:
    load_dotenv()
    p = argparse.ArgumentParser(prog="hackframe")
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

    pc = sub.add_parser("pubchem", help="test the PubChem tool directly (no LLM)")
    pc.add_argument("smiles")

    args = p.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    if args.cmd == "pubchem":
        from hackframe.tools.pubchem import cli_lookup

        asyncio.run(cli_lookup(args.smiles))
        return

    rt = Runtime()

    if args.cmd == "list":
        print(json.dumps(rt.describe(), indent=2))
        return
    if args.cmd == "devui":
        from hackframe.devui.server import launch

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
