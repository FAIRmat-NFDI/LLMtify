"""STAGE-1 `preprocess`: one JSON in (incl. its CIFs) -> local temp/<uuid>.json.

Input  : JSON text of any object, e.g. {"systems": {...}, "cifs": {"results/material/topology/0.cif": "...", ...}}
         (DevUI: paste into the single input box; CLI/API: pass the dict, it is serialized)
Output : {"artifact_id", "path", "bytes"}
Saved via MAF FileSystemAgentFileStore (see core/artifacts.py). The artifact_id travels in
StepMessage.task.params -> every later step uses `message.artifact_id`.

workflows.yaml:
  - {id: preprocess, type: preprocess}
"""

import json
import logging
from typing import Any

from agent_framework import WorkflowContext, handler

from hackframe.core.schemas import StepMessage, TaskInput
from hackframe.executors.base import EXECUTORS, BaseEntryExecutor


log = logging.getLogger(__name__)


@EXECUTORS.register("preprocess")
class PreprocessExecutor(BaseEntryExecutor):
    executor_type = "preprocess"
    # JSON *text*: DevUI renders one input box to paste the whole JSON into
    # (it shows no input at all for a property-less object schema). CLI/API dicts are serialized.
    input_model = str

    @handler
    async def receive(self, text: str, ctx: WorkflowContext[StepMessage]) -> None:
        await self.start(text, ctx)

    async def ingest(self, text: str) -> TaskInput:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"input is not valid JSON: {exc}") from exc
        if not isinstance(data, dict) or not data:
            raise ValueError("input must be a non-empty JSON object")
        if self.artifacts is None:
            raise RuntimeError("no artifact store configured")
        artifact_id = await self.artifacts.save(data)
        return TaskInput(task=f"material {artifact_id}", params={"artifact_id": artifact_id})

    async def process(self, message: StepMessage) -> dict[str, Any]:
        aid = message.artifact_id
        path = self.artifacts.path(aid)
        log.info("[%s] saved %s", self.id, path)
        return {"artifact_id": aid, "path": str(path), "bytes": path.stat().st_size}
