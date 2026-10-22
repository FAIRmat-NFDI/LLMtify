"""WorkflowSpec -> MAF Workflow.

Every workflow:  intake -> [pattern over steps] -> finalize
  sequential : intake -> s1 -> s2 -> ... -> finalize
  fan_out_in : intake => {s1..sn} => merge -> finalize
  graph      : intake => entry steps, custom edges, exit -> finalize
  router     : intake -> pre steps -> router =(switch-case on WorkItem.next_tool)=> tools => gather -> post -> finalize
If the first step is an entry executor (e.g. preprocess) it replaces `intake`
and the workflow input is that executor's `input_model`.
"""

from typing import Any

from agent_framework import Case, Default, Workflow, WorkflowBuilder
from pydantic import BaseModel

from hackframe.core.config import WorkflowSpec
from hackframe.core.schemas import TaskInput, WorkItem
from hackframe.executors import EXECUTORS, BaseStepExecutor, BuildContext
from hackframe.executors.base import ExecutorCore
from hackframe.executors.builtin import FinalizeExecutor, IntakeExecutor, MergeExecutor
from hackframe.executors.routing import GatherExecutor


def _as_list(v: str | list[str]) -> list[str]:
    return v if isinstance(v, list) else [v]


class WorkflowFactory:
    def __init__(self, specs: dict[str, WorkflowSpec], build: BuildContext) -> None:
        self.specs = specs
        self.build = build

    def names(self) -> list[str]:
        return sorted(self.specs)

    def input_model(self, name: str) -> type[BaseModel]:
        """Workflow input type: the entry step's input_model, else TaskInput."""
        first = self.specs[name].steps[0]
        cls = EXECUTORS.get(first.type)
        return cls.input_model if getattr(cls, "is_entry", False) else TaskInput

    def create(self, name: str) -> Workflow:
        spec = self.specs[name]
        steps: dict[str, ExecutorCore] = {
            s.id: EXECUTORS.get(s.type).from_spec(s, self.build) for s in spec.steps
        }
        entries = [s.id for s in spec.steps if steps[s.id].is_entry]
        if entries and entries != [spec.steps[0].id]:
            raise ValueError(f"workflow '{name}': entry step must be the first and only one, got {entries}")

        if spec.output and spec.output not in steps:
            raise ValueError(f"workflow '{name}': output step '{spec.output}' not in steps")
        finalize = FinalizeExecutor(output_step=spec.output, output_field=spec.output_field)
        ordered = [steps[s.id] for s in spec.steps]

        if entries:  # entry step replaces intake and receives the workflow input itself
            intake, ordered = ordered[0], ordered[1:]
            if spec.pattern == "graph":
                raise ValueError("graph workflows with an entry step are not supported; use sequential")
        else:
            intake = IntakeExecutor()
        wb = WorkflowBuilder(name=name, description=spec.description or None, start_executor=intake)

        if spec.pattern == "sequential":
            wb.add_chain([intake, *ordered, finalize])

        elif spec.pattern == "fan_out_in":
            if not ordered:
                wb.add_edge(intake, finalize)
            else:
                merge = MergeExecutor()
                wb.add_fan_out_edges(intake, ordered)
                wb.add_fan_in_edges(ordered, merge)
                wb.add_edge(merge, finalize)

        elif spec.pattern == "graph":
            self._wire_graph(wb, spec, steps, intake, finalize)

        elif spec.pattern == "router":
            self._wire_router(wb, spec, steps, intake, finalize)

        return wb.build()

    @staticmethod
    def _wire_router(
        wb: WorkflowBuilder, spec: WorkflowSpec, steps: dict[str, Any], intake: Any, finalize: Any
    ) -> None:
        """intake/entry -> pre... -> router =(switch-case)=> tools <=> tools => gather -> post... -> finalize"""
        if not spec.router or not spec.tools:
            raise ValueError(f"router workflow '{spec.name}' needs `router` and `tools`")
        router = steps[spec.router]
        tools = {t: steps[t] for t in spec.tools}
        if not router.is_router:
            raise ValueError(f"'{spec.router}' is not a router executor")
        if bad := [t for t, ex in tools.items() if not ex.is_tool]:
            raise ValueError(f"not tool executors: {bad}")
        router.tools = {t: ex.description for t, ex in tools.items()}

        ids = [s.id for s in spec.steps]
        idx = ids.index(spec.router)
        skip = {spec.router, *spec.tools, intake.id}
        pre = [steps[i] for i in ids[:idx] if i not in skip]
        post = [steps[i] for i in ids[idx + 1 :] if i not in skip]
        gather = GatherExecutor(id=spec.gather)

        wb.add_chain([intake, *pre, router])

        def route_to(target: str):
            return lambda m: isinstance(m, WorkItem) and m.next_tool == target

        # the agentic switch: next_tool picks the executor, everything else goes to gather
        wb.add_switch_case_edge_group(
            router, [*(Case(condition=route_to(t), target=ex) for t, ex in tools.items()), Default(target=gather)]
        )
        for t, ex in tools.items():
            wb.add_switch_case_edge_group(
                ex,
                [*(Case(condition=route_to(o), target=oex) for o, oex in tools.items() if o != t), Default(target=gather)],
            )
        wb.add_chain([gather, *post, finalize])

    @staticmethod
    def _wire_graph(
        wb: WorkflowBuilder,
        spec: WorkflowSpec,
        steps: dict[str, BaseStepExecutor],
        intake: Any,
        finalize: Any,
    ) -> None:
        if not spec.entry or not spec.exit:
            raise ValueError(f"graph workflow '{spec.name}' needs `entry` and `exit`")
        entry = [steps[i] for i in spec.entry]
        if len(entry) == 1:
            wb.add_edge(intake, entry[0])
        else:
            wb.add_fan_out_edges(intake, entry)

        for e in spec.edges:
            src, dst = _as_list(e.source), _as_list(e.target)
            if len(src) == 1 and len(dst) == 1:
                wb.add_edge(steps[src[0]], steps[dst[0]])
            elif len(src) == 1:
                wb.add_fan_out_edges(steps[src[0]], [steps[d] for d in dst])
            elif len(dst) == 1:
                # fan-in needs a join; insert a merge node named after the target
                join = MergeExecutor(id=f"join__{dst[0]}")
                wb.add_fan_in_edges([steps[s] for s in src], join)
                wb.add_edge(join, steps[dst[0]])
            else:
                raise ValueError("many-to-many edges: split into fan-in + fan-out")

        wb.add_edge(steps[spec.exit], finalize)
