def custom_cif_to_sequence(cif_file):
    import sys, numpy as np
    from collections import Counter
    from scipy.spatial import cKDTree

    def read_cif(path):
        el, xyz = [], []
        for l in open(path):
            p = l.split()
            if len(p) == 7 and p[2] == '1.0' or (len(p) >= 7 and p[0] in ('N','H','C','O','S') and p[1][0] == p[0]):
                try:
                    xyz.append([float(p[3]), float(p[4]), float(p[5])]); el.append(p[0])
                except ValueError: pass
        return el, np.array(xyz)

    RAD = {'H':0.31,'C':0.76,'N':0.71,'O':0.66,'S':1.05}
    el, xyz = read_cif(sys.argv[1])
    n = len(el)
    tree = cKDTree(xyz)
    adj = [set() for _ in range(n)]
    for i, j in tree.query_pairs(2.2):
        if el[i]=='H' and el[j]=='H': continue
        if np.linalg.norm(xyz[i]-xyz[j]) < RAD[el[i]]+RAD[el[j]]+0.4:
            adj[i].add(j); adj[j].add(i)

    nb = lambda i, e: [j for j in adj[i] if el[j]==e]
    # carbonyl carbons: C with one O (terminal, no further heavy nbrs), bonded to N and C
    carbonyls = []
    for i in range(n):
        if el[i]=='C':
            Os=[o for o in nb(i,'O') if len([x for x in adj[o] if el[x]!='H'])==1]
            if len(Os)>=1 and len(nb(i,'N'))>=1 and len(nb(i,'C'))>=1: carbonyls.append(i)
    # For each N-terminal-side: CA = C neighbour of carbonyl that is bonded to an N
    res = []  # (CA, N, Cc)
    for c in carbonyls:
        for ca in nb(c,'C'):
            for N in nb(ca,'N'):
                if N in adj[c]: continue
                if any(el[x]=='O' for x in adj[N]): continue
                res.append((ca,N,c))
    # C-terminal carboxylate residue
    for c in range(n):
        if el[c]=='C' and len(nb(c,'O'))==2 and len(nb(c,'N'))==0 and len(nb(c,'C'))==1:
            ca = nb(c,'C')[0]
            for N in nb(ca,'N'):
                res.append((ca,N,c))
    res = list({r[0]:r for r in res}.values())

    def sidechain(ca,N,c):
        seen={ca,N,c}; stack=[x for x in adj[ca] if x not in seen]; sc=[]
        while stack:
            a=stack.pop()
            if a in seen: continue
            seen.add(a); sc.append(a); stack += [x for x in adj[a] if x not in seen]
        return sc

    def sig(sc, ca):
        hv=[a for a in sc if el[a]!='H']; S=set(hv)|{ca}
        d={a:[x for x in adj[a] if x in S] for a in S}
        lab={a:(el[a],len(d[a]),a==ca) for a in S}
        for _ in range(4):
            lab={a:hash((lab[a],tuple(sorted(lab[x] for x in d[a])))) for a in S}
        return (len(hv), tuple(sorted(lab.values())))
    # reference signatures derived from heavy-atom side-chain topology
    REF = {
    'G':[], 'A':['C'], 'S':['C','O'],'C':['C','S'],
    }
    def ref_sig(atoms, bonds):
        d={a:[] for a in atoms}
        for a,b in bonds: d[a].append(b); d[b].append(a)
        lab={a:(a[0],len(d[a]),a=='CA') for a in atoms}
        for _ in range(4):
            lab={a:hash((lab[a],tuple(sorted(lab[x] for x in d[a])))) for a in atoms}
        return (len(atoms)-1, tuple(sorted(lab.values())))
    T={ # atoms (without CA) and bonds incl. CA
    'A':(['CB'],[('CA','CB')]),
    'S':(['CB','OG'],[('CA','CB'),('CB','OG')]),
    'C':(['CB','SG'],[('CA','CB'),('CB','SG')]),
    'V':(['CB','CG1','CG2'],[('CA','CB'),('CB','CG1'),('CB','CG2')]),
    'T':(['CB','OG1','CG2'],[('CA','CB'),('CB','OG1'),('CB','CG2')]),
    'L':(['CB','CG','CD1','CD2'],[('CA','CB'),('CB','CG'),('CG','CD1'),('CG','CD2')]),
    'I':(['CB','CG1','CG2','CD1'],[('CA','CB'),('CB','CG1'),('CB','CG2'),('CG1','CD1')]),
    'D':(['CB','CG','OD1','OD2'],[('CA','CB'),('CB','CG'),('CG','OD1'),('CG','OD2')]),
    'N':(['CB','CG','OD1','ND2'],[('CA','CB'),('CB','CG'),('CG','OD1'),('CG','ND2')]),
    'M':(['CB','CG','SD','CE'],[('CA','CB'),('CB','CG'),('CG','SD'),('SD','CE')]),
    'E':(['CB','CG','CD','OE1','OE2'],[('CA','CB'),('CB','CG'),('CG','CD'),('CD','OE1'),('CD','OE2')]),
    'Q':(['CB','CG','CD','OE1','NE2'],[('CA','CB'),('CB','CG'),('CG','CD'),('CD','OE1'),('CD','NE2')]),
    'K':(['CB','CG','CD','CE','NZ'],[('CA','CB'),('CB','CG'),('CG','CD'),('CD','CE'),('CE','NZ')]),
    'R':(['CB','CG','CD','NE','CZ','NH1','NH2'],[('CA','CB'),('CB','CG'),('CG','CD'),('CD','NE'),('NE','CZ'),('CZ','NH1'),('CZ','NH2')]),
    'H':(['CB','CG','ND1','CD2','CE1','NE2'],[('CA','CB'),('CB','CG'),('CG','ND1'),('CG','CD2'),('ND1','CE1'),('CD2','NE2'),('CE1','NE2')]),
    'F':(['CB','CG','CD1','CD2','CE1','CE2','CZ'],[('CA','CB'),('CB','CG'),('CG','CD1'),('CG','CD2'),('CD1','CE1'),('CD2','CE2'),('CE1','CZ'),('CE2','CZ')]),
    'Y':(['CB','CG','CD1','CD2','CE1','CE2','CZ','OH'],[('CA','CB'),('CB','CG'),('CG','CD1'),('CG','CD2'),('CD1','CE1'),('CD2','CE2'),('CE1','CZ'),('CE2','CZ'),('CZ','OH')]),
    'W':(['CB','CG','CD1','CD2','NE1','CE2','CE3','CZ2','CZ3','CH2'],[('CA','CB'),('CB','CG'),('CG','CD1'),('CG','CD2'),('CD1','NE1'),('NE1','CE2'),('CD2','CE2'),('CD2','CE3'),('CE2','CZ2'),('CE3','CZ3'),('CZ2','CH2'),('CZ3','CH2')]),
    }
    SIG={}
    for k,(a,b) in T.items(): SIG[ref_sig(a+['CA'],b)]=k
    SIG[(0,tuple(sorted([hash((('C',0),()))])))]='G'

    # order residues along the chain via peptide bonds: res i's carbonyl C bonded to res j's N
    byN={r[1]:r for r in res}
    nxt={}
    for r in res:
        for x in adj[r[2]]:
            if x in byN: nxt[r[0]]=byN[x][0]
    has_prev=set(nxt.values())
    starts=[r[0] for r in res if r[0] not in has_prev]
    byCA={r[0]:r for r in res}
    out=[]
    for s in starts:
        seq=[]; ca=s
        while True:
            ca_,N,c=byCA[ca]; sc=sidechain(ca_,N,c)
            s_=sig(sc,ca_)
            if not sc or not [a for a in sc if el[a]!='H']: aa='G'
            else: aa=SIG.get(s_)
            # proline: side chain loops back to N
            if aa is None:
                hv=[a for a in sc if el[a]!='H']
                if N in [x for a in hv for x in adj[a]] : aa='P'
                else: aa='X'
            seq.append(aa)
            if ca in nxt: ca=nxt[ca]
            else: break
        out.append(''.join(seq))
    print(f"residues found: {len(res)}, chains: {len(out)}", file=sys.stderr)
    for i,s in enumerate(out): print(f">chain{i+1} len={len(s)}\n{s}")

def cif_to_sequence(cif_string):
    from io import StringIO
    from openbabel import pybel
    from Bio import SeqIO

    mol = pybel.readstring("cif", cif_string)
    pdb_string = mol.write("pdb")   # returns a string when no filename is given

    seq = ""
    for rec in SeqIO.parse(StringIO(pdb_string), "pdb-atom"):
        seq += str(rec.seq)
    return seq