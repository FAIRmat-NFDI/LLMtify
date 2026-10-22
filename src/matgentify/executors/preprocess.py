"""STAGE-1 `preprocess`: input -> material JSON {systems, cifs} -> local temp/<uuid>.json.

Input (JSON text; DevUI: one input box, CLI/API: a dict is serialized), any of:
  {"entry": "<NOMAD entry URL or entry_id>"}    -> fetched from the NOMAD API and converted (tools/nomad.py)
  "<NOMAD entry URL or entry_id>"               -> same, without the JSON wrapper
  {"systems": {...}, "cifs": {...}}             -> used as is
Optional field (with entry or full JSON): "metainfo": true | false   (default true)
  false -> before saving, every subsystem gets label, structural_type, building_block = null
           (the pipeline then has to judge the material from formula/elements/CIFs alone)
Output : {"artifact_id", "path", "bytes", "source", "metainfo", "entry_id"?}
Saved via MAF FileSystemAgentFileStore (see core/artifacts.py). The artifact_id travels in
StepMessage.task.params -> every later step uses `message.artifact_id`.

workflows.yaml:
  - {id: preprocess, type: preprocess}
"""

import asyncio
import json
import logging
from typing import Any

from agent_framework import WorkflowContext, handler

from matgentify.core.material import system_meta
from matgentify.core.schemas import StepMessage, TaskInput
from matgentify.executors.base import EXECUTORS, BaseEntryExecutor
from matgentify.tools import nomad

log = logging.getLogger(__name__)

_ENTRY_KEYS = ("entry", "entry_id", "url")
META_FIELDS = ("label", "structural_type", "building_block")  # nulled when metainfo is false


def parse_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "0", "no", "off", "")
    return bool(value)


def strip_metainfo(data: dict[str, Any]) -> dict[str, Any]:
    """Set META_FIELDS to null in every subsystem (nested `metadata` or flat layout)."""
    for entry in (data.get("systems") or {}).values():
        meta = system_meta(entry)
        for field in META_FIELDS:
            meta[field] = None
    return data


@EXECUTORS.register("preprocess")
class PreprocessExecutor(BaseEntryExecutor):
    executor_type = "preprocess"
    # JSON *text*: DevUI renders one input box (it shows no input at all for a property-less object schema).
    input_model = str

    @handler
    async def receive(self, text: str, ctx: WorkflowContext[StepMessage]) -> None:
        await self.start(text, ctx)

    async def fetch_entry(self, ref: str) -> dict[str, Any]:
        """NOMAD entry URL/id -> material JSON (blocking HTTP runs in a thread)."""
        entry_id = nomad.parse_entry_id(ref)
        log.info("[%s] fetching NOMAD entry %s", self.id, entry_id)
        archive = await asyncio.to_thread(nomad.fetch_archive, entry_id)
        return nomad.archive_to_material(archive, entry_id)

    async def ingest(self, text: str) -> TaskInput:
        stripped = text.strip()
        try:
            data: Any = json.loads(stripped)
        except json.JSONDecodeError as exc:
            if stripped[:1] in "{[\"":
                raise ValueError(f"input is not valid JSON: {exc}") from exc
            data = stripped  # bare URL / entry id

        source = "json"
        metainfo = parse_bool(data.pop("metainfo", None)) if isinstance(data, dict) else True
        if isinstance(data, str):
            data, source = await self.fetch_entry(data), "nomad"
        elif isinstance(data, dict) and "systems" not in data and (ref := next((data[k] for k in _ENTRY_KEYS if data.get(k)), None)):
            data, source = await self.fetch_entry(str(ref)), "nomad"
        if not isinstance(data, dict) or not data:
            raise ValueError("input must be a non-empty JSON object, or {\"entry\": \"<NOMAD URL or entry_id>\"}")
        if self.artifacts is None:
            raise RuntimeError("no artifact store configured")
        if not metainfo:
            strip_metainfo(data)
            log.info("[%s] metainfo=false: %s set to null in all subsystems", self.id, ", ".join(META_FIELDS))
        artifact_id = await self.artifacts.save(data)
        params = {"artifact_id": artifact_id, "source": source, "metainfo": metainfo}
        if data.get("entry_id"):
            params["entry_id"] = data["entry_id"]
        return TaskInput(task=f"material {artifact_id}", params=params)

    async def process(self, message: StepMessage) -> dict[str, Any]:
        aid = message.artifact_id
        path = self.artifacts.path(aid)
        log.info("[%s] saved %s", self.id, path)
        out = {"artifact_id": aid, "path": str(path), "bytes": path.stat().st_size, "source": message.task.params.get("source"),
               "metainfo": message.task.params.get("metainfo", True)}
        if entry := message.task.params.get("entry_id"):
            out["entry_id"] = entry
        return out
