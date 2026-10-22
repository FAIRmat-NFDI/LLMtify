"""STAGE-2 `compact`: temp/<uuid>.json -> small version without CIF bodies.

Output : the full JSON minus `cifs`, plus
           cif_keys    : CIF names available in the artifact
           system_cif  : system key -> its CIF key (only systems that have one)
CIFs stay in temp/<uuid>.json; later executors resolve them on demand:

    cif = await self.artifacts.get_cif(message.artifact_id, "results/material/topology/3")

workflows.yaml:
  - {id: compact, type: compact}
"""

import logging
from typing import Any

from matgentify.core.material import cif_key
from matgentify.core.schemas import StepMessage
from matgentify.executors.base import EXECUTORS, BaseStepExecutor


log = logging.getLogger(__name__)


@EXECUTORS.register("compact")
class CompactExecutor(BaseStepExecutor):
    executor_type = "compact"

    async def process(self, message: StepMessage) -> dict[str, Any]:
        data = await self.artifacts.load(message.artifact_id)
        cifs = data.pop("cifs", {}) or {}
        systems = data.get("systems", {}) or {}
        small = {
            "artifact_id": message.artifact_id,
            **data,
            "cif_keys": sorted(cifs),
            "system_cif": {s: k for s in systems if (k := cif_key(s, cifs))},
        }
        log.info("[%s] %d systems, %d cifs stripped (resolve via artifacts.get_cif)", self.id, len(systems), len(cifs))
        return small
