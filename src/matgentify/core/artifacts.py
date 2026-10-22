"""Run artifacts on disk via MAF's FileSystemAgentFileStore.

preprocess writes the full input JSON to  temp/<artifact_id>.json ; later executors
resolve pieces from it (e.g. one CIF) instead of carrying big payloads along edges.

    store = ArtifactStore("temp")
    aid   = await store.save(full_json)               # -> "3f2c...uuid"
    data  = await store.load(aid)                     # full JSON
    cif   = await store.get_cif(aid, "results/material/topology/3")
"""

import json
import uuid
import warnings
from pathlib import Path
from typing import Any

from agent_framework import FileSystemAgentFileStore

from matgentify.core.material import cif_key

warnings.filterwarnings("ignore", message=r".*FileSystemAgentFileStore is experimental.*")


class ArtifactNotFound(KeyError):
    pass


class ArtifactStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self._fs = FileSystemAgentFileStore(self.root)  # rejects ../ and symlinks

    @staticmethod
    def _name(artifact_id: str) -> str:
        return f"{uuid.UUID(artifact_id)}.json"  # validates id format, no path tricks

    def path(self, artifact_id: str) -> Path:
        return self.root / self._name(artifact_id)

    async def save(self, data: dict[str, Any], artifact_id: str | None = None) -> str:
        artifact_id = artifact_id or str(uuid.uuid4())
        await self._fs.write(self._name(artifact_id), json.dumps(data), overwrite=False)
        return artifact_id

    async def load(self, artifact_id: str) -> dict[str, Any]:
        raw = await self._fs.read(self._name(artifact_id))
        if raw is None:
            raise ArtifactNotFound(f"artifact {artifact_id} not found in {self.root}")
        return json.loads(raw)

    async def get_cif(self, artifact_id: str, key: str) -> str:
        """`key` with or without '.cif', e.g. 'results/material/topology/3'."""
        cifs = (await self.load(artifact_id)).get("cifs") or {}
        name = cif_key(key.removesuffix(".cif"), cifs)
        if name is None:
            raise ArtifactNotFound(f"cif '{key}' not in artifact {artifact_id}; have {sorted(cifs)}")
        return cifs[name]

    async def delete(self, artifact_id: str) -> bool:
        return await self._fs.delete(self._name(artifact_id))
