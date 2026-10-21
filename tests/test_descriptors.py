import numpy as np
from ase import Atoms
from rdkit import Chem

from llmtify.descriptors import ase_to_rdkit, atoms_to_smiles


class TestAseToRdkit:
    """Tests for ASE to RDKit conversion functions."""

    def test_methane_conversion(self):
        """Test basic tetrahedral CH4 molecule."""
        methane = Atoms(
            'CH4',
            positions=[
                [0.0, 0.0, 0.0],
                [0.63, 0.63, 0.63],
                [-0.63, -0.63, 0.63],
                [-0.63, 0.63, -0.63],
                [0.63, -0.63, -0.63],
            ]
        )
        mol = ase_to_rdkit(methane)
        
        assert mol.GetNumAtoms() == 5
        assert mol.GetNumBonds() == 4
        assert all(b.GetBondType() == Chem.BondType.SINGLE for b in mol.GetBonds())
        
        # Check atom types preserved
        symbols = [mol.GetAtomWithIdx(i).GetSymbol() for i in range(5)]
        assert symbols.count('C') == 1
        assert symbols.count('H') == 4

    def test_ethane_conversion(self):
        """Test C-C bond detection in ethane."""
        ethane = Atoms(
            'C2H6',
            positions=[
                [0.0, 0.0, 0.0],      # C1
                [1.54, 0.0, 0.0],      # C2 (typical C-C length)
                [0.5, 0.9, 0.4],       # H
                [0.5, -0.9, 0.4],
                [-0.9, 0.0, 0.5],
                [2.0, 0.9, -0.4],
                [2.0, -0.9, -0.4],
                [2.4, 0.0, -0.5],
            ]
        )
        mol = ase_to_rdkit(ethane)
        
        # Should find 1 C-C bond and 6 C-H bonds
        bonds = [(b.GetBeginAtomIdx(), b.GetEndAtomIdx()) for b in mol.GetBonds()]
        cc_bonds = sum(
            1 for b in mol.GetBonds()
            if {b.GetBeginAtom().GetSymbol(), b.GetEndAtom().GetSymbol()} == {'C'}
        )
        assert cc_bonds == 1
        assert mol.GetNumBonds() == 7

    def test_tolerance_parameter(self):
        """Test that tolerance controls bond detection sensitivity."""
        # Two carbons at distance where 1.3*r_cov excludes but 1.5*r_cov includes
        r_cov_c = 0.76  # covalent radius of carbon in Angstrom
        dist = 1.4 * r_cov_c * 2  # Between 1.3 and 1.5 times sum of radii
        
        dimer = Atoms('C2', positions=[[0, 0, 0], [dist, 0, 0]])
        
        mol_strict = ase_to_rdkit(dimer, tolerance=1.3)
        mol_loose = ase_to_rdkit(dimer, tolerance=1.5)
        
        assert mol_strict.GetNumBonds() == 0
        assert mol_loose.GetNumBonds() == 1

    def test_no_self_bonding(self):
        """Ensure no atom bonds to itself."""
        mol = Atoms('H', positions=[[0, 0, 0]])
        result = ase_to_rdkit(mol)
        
        assert result.GetNumBonds() == 0

    def test_periodic_boundary_conditions(self):
        """Test MIC handling with periodic cell."""
        # Two atoms across periodic boundary
        atoms = Atoms(
            'Na2',
            positions=[[0.1, 0.5, 0.5], [3.9, 0.5, 0.5]],
            cell=[4.0, 1.0, 1.0],
            pbc=True
        )
        mol = ase_to_rdkit(atoms)
        
        # MIC distance is 0.2, direct is 3.8
        # Should bond due to MIC
        assert mol.GetNumBonds() == 1

    def test_isolated_atoms_no_bonds(self):
        """Test widely separated atoms form no bonds."""
        isolated = Atoms('HH', positions=[[0, 0, 0], [10, 0, 0]])
        mol = ase_to_rdkit(isolated)
        
        assert mol.GetNumBonds() == 0

    def test_metal_cluster(self):
        """Test with transition metal (different covalent radius lookup)."""
        pt_cluster = Atoms(
            'Pt4',
            positions=[
                [0, 0, 0],
                [2.8, 0, 0],
                [1.4, 2.4, 0],
                [1.4, 0.8, 2.2],
            ],
            cell=[10, 10, 10]
        )
        mol = ase_to_rdkit(pt_cluster)
        
        assert mol.GetNumAtoms() == 4
        # Pt-Pt bonds depend on geometry; just verify no crash
        assert all(a.GetSymbol() == 'Pt' for a in mol.GetAtoms())


class TestAtomsToSmiles:
    """Tests for SMILES generation wrapper."""

    def test_methane_smiles(self):
        """Test canonical SMILES for methane."""
        methane = Atoms('CH4', positions=[
            [0, 0, 0], [0.63, 0.63, 0.63],
            [-0.63, -0.63, 0.63], [-0.63, 0.63, -0.63],
            [0.63, -0.63, -0.63]
        ])
        smiles = atoms_to_smiles(methane)
        
        assert smiles == '[H]C([H])([H])[H]'
        # Verify RDKit can parse it back
        assert Chem.MolFromSmiles(smiles) is not None

    def test_ethane_smiles(self):
        """Test ethane gives valid SMILES."""
        ethane = Atoms(
            'C2H6',
            positions=[
                [0, 0, 0], [1.54, 0, 0],
                [0.5, 0.9, 0.4], [0.5, -0.9, 0.4],
                [-0.9, 0, 0.5], [2.0, 0.9, -0.4],
                [2.0, -0.9, -0.4], [2.4, 0, -0.5],
            ]
        )
        smiles = atoms_to_smiles(ethane)
        
        # Should be CC or equivalent
        mol_back = Chem.MolFromSmiles(smiles)
        assert mol_back is not None
        assert mol_back.GetNumAtoms() == 2  # Heavy atoms only in SMILES
        assert all(a.GetSymbol() == 'C' for a in mol_back.GetAtoms())

    def test_water_smiles(self):
        """Test H2O SMILES generation."""
        water = Atoms('H2O', positions=[
            [0, 0, 0],
            [0.96, 0, 0],
            [-0.24, 0.93, 0]
        ])
        smiles = atoms_to_smiles(water)
        
        # RDKit typically gives [H]O[H] or O for water
        assert Chem.MolFromSmiles(smiles) is not None

    def test_unusual_element(self):
        """Test element not in organic chemistry context."""
        # Xenon dimer (no bond expected, but should not crash)
        xe = Atoms('Xe2', positions=[[0, 0, 0], [5, 0, 0]])
        smiles = atoms_to_smiles(xe)
        
        # Should produce something parseable, likely [Xe].[Xe]
        assert Chem.MolFromSmiles(smiles) is not None

    def test_benzene_ring_perception(self):
        """Test aromatic ring detection in planar benzene."""
        # Simplified benzene coordinates (regular hexagon, planar)
        a = 1.39  # C-C distance
        benzene = Atoms('C6H6', positions=[
            [a, 0, 0],
            [a/2, a*0.866, 0],
            [-a/2, a*0.866, 0],
            [-a, 0, 0],
            [-a/2, -a*0.866, 0],
            [a/2, -a*0.866, 0],
            [2.2, 0, 0],      # H attached to C1
            [1.1, 1.9, 0],
            [-1.1, 1.9, 0],
            [-2.2, 0, 0],
            [-1.1, -1.9, 0],
            [1.1, -1.9, 0],
        ])
        smiles = atoms_to_smiles(benzene)
        
        # RDKit may or may not perceive aromaticity from 3D with single bonds
        # Main check: valid SMILES, correct heavy atom count
        mol = Chem.MolFromSmiles(smiles)
        assert mol is not None
        assert mol.GetNumAtoms() == 6  # SMILES typically omits H


class TestIntegrationEdgeCases:
    """Edge cases and robustness tests."""

    def test_fractional_occupancy_not_supported(self):
        """ASE atoms with tags/masses should not affect conversion."""
        atoms = Atoms('CO', positions=[[0, 0, 0], [1.2, 0, 0]])
        atoms.set_tags([1, 2])
        atoms.set_masses([12.0, 16.0])
        
        mol = ase_to_rdkit(atoms)
        assert mol.GetNumAtoms() == 2

    def test_large_system_performance(self):
        """Smoke test for moderately large system."""
        np.random.seed(42)
        n = 100
        pos = np.random.rand(n, 3) * 20
        atoms = Atoms('H' * n, positions=pos)
        
        mol = ase_to_rdkit(atoms)
        assert mol.GetNumAtoms() == n
        # O(n²) bond check; mainly ensuring it completes