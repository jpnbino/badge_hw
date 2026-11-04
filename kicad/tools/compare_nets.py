"""Compare schematic netlist (kicad xml) with the imported PCB pad->net assignment. Run with KiCad python from kicad/."""
import sys, xml.etree.ElementTree as ET, pcbnew
sch = {}
for net in ET.parse("n.xml").getroot().iter("net"):
    for node in net.iter("node"):
        sch[(node.get("ref"), node.get("pin"))] = net.get("name").lstrip("/")
b = pcbnew.LoadBoard("BADGE.kicad_pcb")
pcb = {}
for f in b.GetFootprints():
    for p in f.Pads():
        if f.GetReference() and p.GetNetname():
            pcb[(f.GetReference(), p.GetNumber())] = p.GetNetname().lstrip("/")
# group by net: compare partitions (names may differ for unnamed nets)
def part(d):
    g = {}
    for k, v in d.items():
        g.setdefault(v, set()).add(k)
    return g
ps, pp = part(sch), part(pcb)
sets_s = {frozenset(v) for v in ps.values()}
sets_p = {frozenset(v) for v in pp.values()}
print("sch nets", len(ps), "pcb nets", len(pp))
print("only in sch:", [sorted(s) for s in sets_s - sets_p])
print("only in pcb:", [sorted(s) for s in sets_p - sets_s])
name_diff = [(n, n2) for n, s in ps.items() for n2, s2 in pp.items() if set(s) == set(s2) and n != n2]
print("renamed:", name_diff)
print("missing in sch:", sorted(set(pcb) - set(sch)))
print("missing in pcb:", sorted(set(sch) - set(pcb)))
