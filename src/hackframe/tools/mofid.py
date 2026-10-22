"""MOFid (snurr-group/mofid, run from mofid.sif via Singularity) - domain team's parser and runner.

Setup: ./run.sh mofid  (installs singularity-ce, downloads external/mofid.sif)
Env:   MOFID_SIF (default external/mofid.sif), MOFID_SINGULARITY_FLAGS (e.g. "--userns" in containers)
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field, computed_field

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
    topology: Optional[str] = Field(None, description="RCSR code(s); None if generator returned ERROR/NA")
    catenation: Optional[int] = Field(None, description="Interpenetration count reported by generator")
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


PROJECT_ROOT = Path(__file__).resolve().parents[3]  # src/hackframe/tools/mofid.py -> project root


def sif_candidates() -> list[Path]:
    """Where mofid.sif is looked for (resolved at call time, so MOFID_SIF from .env is honoured)."""
    out = []
    if env := os.environ.get("MOFID_SIF"):
        p = Path(env).expanduser()
        out += [p] if p.is_absolute() else [Path.cwd() / p, PROJECT_ROOT / p]
    out += [PROJECT_ROOT / "external" / "mofid.sif", Path.cwd() / "external" / "mofid.sif",
            Path.home() / "Downloads" / "mofid.sif", Path.home() / "mofid.sif"]
    return list(dict.fromkeys(c.resolve() for c in out))


def find_sif() -> Path:
    for c in sif_candidates():
        if c.is_file():
            return c
    raise MOFidError("mofid.sif not found. Run ./run.sh mofid (downloads it to external/mofid.sif) "
                     "or set MOFID_SIF=/abs/path/mofid.sif in .env. Looked in: "
                     + ", ".join(str(c) for c in sif_candidates()))


class MOFidError(RuntimeError):
    pass


def check_installed(sif: Path | None = None) -> Path:
    if not shutil.which("singularity"):
        raise MOFidError("singularity not installed - run: ./run.sh mofid")
    if sif is None:
        return find_sif()
    if not Path(sif).is_file():
        raise MOFidError(f"{sif} not found - run: ./run.sh mofid (or set MOFID_SIF)")
    return Path(sif)


def get_mofid(cif_string: str, sif: Path | None = None, timeout: float = 600) -> dict:
    """Run MOFid on a CIF string. Returns MOFid's JSON plus `mofid_parsed`. Raises MOFidError."""
    sif = check_installed(sif)
    flags = shlex.split(os.environ.get("MOFID_SINGULARITY_FLAGS", ""))
    with tempfile.TemporaryDirectory() as tmp:
        cif = Path(tmp) / "input.cif"
        cif.write_text(cif_string)

        def run(extra: list[str]) -> subprocess.CompletedProcess:
            cmd = ["singularity", "run", *extra, str(sif), "file", str(cif)]
            return subprocess.run(
                cmd,
                cwd=tmp,  # any output folder is created here
                universal_newlines=True,
                stdin=subprocess.DEVNULL,  # servers / pytest have no usable stdin
                # openbabel-wheel (cif_to_seq) sets BABEL_* in this process; they would point the container's
                # Open Babel at the host plugins and MOFid then reads nothing ('* MOFid-v1.NA.NA')
                env={k: v for k, v in os.environ.items() if not k.startswith("BABEL")},
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
            )

        result = run(flags)
        if result.returncode != 0 and "capability" in result.stderr and "--userns" not in flags:
            result = run([*flags, "--userns"])  # setuid mode not allowed (containers): unprivileged mode
    if result.stderr:
        sys.stderr.write(result.stderr)  # re-forward C++ errors
    if result.returncode != 0:
        raise MOFidError(f"MOFid failed ({result.returncode}): {result.stderr.strip()[-300:]}")
    mof_id_result = json.loads(result.stdout)
    try:
        mof_id_result["mofid_parsed"] = MOFid.parse(mof_id_result["mofid"]).model_dump()
    except ValueError as exc:  # e.g. '* MOFid-v1.NA.NA' = MOFid could not read the structure
        raise MOFidError(f"MOFid returned no identifier ({mof_id_result.get('mofid')!r})") from exc
    return mof_id_result
