from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, computed_field

import json
import sys
import subprocess
import tempfile
from pathlib import Path

# Atom tokens in SMILES: bracket atoms or organic-subset atoms
_BRACKET = re.compile(r"\[([^\]]+)\]")
_ELEMENT = re.compile(r"(?<![A-Za-z])(Cl|Br|[BCNOSPFI]|[bcnosp])")
_MOFID = re.compile(
    r"^(?P<smiles>\S*)\s+MOFid-(?P<version>v\d+)\."
    r"(?P<topology>.+?)\.cat(?P<cat>\d+|NA)"
    r"(?:;(?P<name>.*))?$"
)
_BAD_TOPOLOGY = {"ERROR", "NA", ""}


class Component(BaseModel):
    """One dot-separated fragment of the MOFid SMILES."""

    smiles: str

    @computed_field
    @property
    def has_carbon(self) -> bool:
        # strip bracket atoms like [Zn], keep [C-], [CH2] etc. via inner check
        for inner in _BRACKET.findall(self.smiles):
            if re.match(r"^\d*(C|c)(?![a-z])", inner):
                return True
        rest = _BRACKET.sub("", self.smiles)
        return any(m.group(1) in ("C", "c") for m in _ELEMENT.finditer(rest))

    @computed_field
    @property
    def role(self) -> Literal["node", "linker"]:
        """Heuristic: no carbon -> inorganic node, otherwise organic linker."""
        return "linker" if self.has_carbon else "node"

    def to_rdkit(self):
        """Return an RDKit Mol (requires rdkit)."""
        from rdkit import Chem

        return Chem.MolFromSmiles(self.smiles)

    def formula(self) -> Optional[str]:
        try:
            from rdkit.Chem.rdMolDescriptors import CalcMolFormula

            mol = self.to_rdkit()
            return CalcMolFormula(mol) if mol else None
        except ImportError:
            return None


class MOFid(BaseModel):
    raw: str
    version: str = "v1"
    components: list[Component]
    topology: Optional[str] = Field(
        None, description="RCSR code(s); None if generator returned ERROR/NA"
    )
    catenation: Optional[int] = Field(
        None, description="Interpenetration count reported by generator"
    )
    name: Optional[str] = Field(None, description="Identifier after ';' (often CIF name)")
    topology_failed: bool = False

    @classmethod
    def parse(cls, s: str) -> "MOFid":
        s = s.strip()
        m = _MOFID.match(s)
        if not m:
            raise ValueError(f"Not a valid MOFid string: {s!r}")
        topo = m["topology"]
        failed = topo in _BAD_TOPOLOGY
        cat = m["cat"]
        smiles = m["smiles"]
        return cls(
            raw=s,
            version=m["version"],
            components=[Component(smiles=x) for x in smiles.split(".") if x],
            topology=None if failed else topo,
            # cat is not meaningful when topology failed
            catenation=None if failed or cat == "NA" else int(cat),
            name=m["name"] or None,
            topology_failed=failed,
        )

    @computed_field
    @property
    def nodes(self) -> list[str]:
        return [c.smiles for c in self.components if c.role == "node"]

    @computed_field
    @property
    def linkers(self) -> list[str]:
        return [c.smiles for c in self.components if c.role == "linker"]

MOFID_SIF = Path("external/mofid.sif").resolve()
def get_mofid(cif_string: str) -> dict:
    
    with tempfile.TemporaryDirectory() as tmp:
        with open(Path(tmp) / "input.cif", "w") as f:
            f.write(cif_string)
        cif_path = Path(tmp) / "input.cif"
        cif = Path(cif_path).resolve()          # absolute, because cwd changes below
        cmd = ["singularity", "run", str(MOFID_SIF), "file", str(cif)]
        run = subprocess.run(
            cmd,
            cwd=tmp,                         # any output folder is created here
            universal_newlines=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    if run.stderr:
        sys.stderr.write(run.stderr)         # re-forward C++ errors
    if run.returncode != 0:
        return f"MOFid failed ({run.returncode}) for {cif.name}"
    mof_id_result=json.loads(run.stdout)
    mof_id_result['mofid_parsed'] = MOFid.parse(mof_id_result["mofid"]).model_dump()
    return mof_id_result
# json.dump(get_mofid("AgC38N4H28.cif"), open('AgC38N4H28.mofid.json', 'w'), indent=2)
# json.dump(get_mofid("AgC26N4H28.cif"), open('AgC26N4H28.mofid.json', 'w'), indent=2)
# json.dump(get_mofid(open('AgC38N4H28.cif').read()), open('AgC38N4H28.mofid.json', 'w'), indent=2)
