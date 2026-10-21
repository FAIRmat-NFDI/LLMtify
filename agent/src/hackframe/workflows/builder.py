"""WorkflowSpec -> MAF Workflow.

Every workflow:  intake -> [pattern over steps] -> finalize
  sequential : intake -> s1 -> s2 -> ... -> finalize
  fan_out_in : intake => {s1..sn} => merge -> finalize
  graph      : intake => entry steps, custom edges, exit -> finalize
"""

from typing import Any

from agent_framework import Workflow, WorkflowBuilder

from hackframe.core.config import WorkflowSpec
from hackframe.executors import EXECUTORS, BaseStepExecutor, BuildContext
from hackframe.executors.builtin import FinalizeExecutor, IntakeExecutor, MergeExecutor


def _as_list(v: str | list[str]) -> list[str]:
    return v if isinstance(v, list) else [v]


class WorkflowFactory:
    def __init__(self, specs: dict[str, WorkflowSpec], build: BuildContext) -> None:
        self.specs = specs
        self.build = build

    def names(self) -> list[str]:
        return sorted(self.specs)

    def create(self, name: str) -> Workflow:
        spec = self.specs[name]
        steps: dict[str, BaseStepExecutor] = {
            s.id: EXECUTORS.get(s.type).from_spec(s, self.build) for s in spec.steps
        }
        intake, finalize = IntakeExecutor(), FinalizeExecutor()
        wb = WorkflowBuilder(name=name, description=spec.description or None, start_executor=intake)

        ordered = [steps[s.id] for s in spec.steps]
        if spec.pattern == "sequential":
            wb.add_chain([intake, *ordered, finalize])

        elif spec.pattern == "fan_out_in":
            merge = MergeExecutor()
            wb.add_fan_out_edges(intake, ordered)
            wb.add_fan_in_edges(ordered, merge)
            wb.add_edge(merge, finalize)

        elif spec.pattern == "graph":
            self._wire_graph(wb, spec, steps, intake, finalize)

        return wb.build()

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
