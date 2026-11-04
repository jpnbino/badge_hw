"""Convert chico.PcbLib + footprints used on BADGE.kicad_pcb into BADGE.pretty and relink the board.
Run with KiCad's bundled python from the kicad/ directory."""
import os, pcbnew
lib = "BADGE.pretty"
os.makedirs(lib, exist_ok=True)
b = pcbnew.LoadBoard("BADGE.kicad_pcb")
saved = {}
io = pcbnew.PCB_IO_KICAD_SEXPR(0)

def save(fp, name):
    key = name.lower()
    if key in saved:
        return saved[key]
    fp.SetFPID(pcbnew.LIB_ID("BADGE", name))
    io.FootprintSave(os.path.abspath(lib), fp)
    saved[key] = name
    return name

for f in b.GetFootprints():
    n = str(f.GetFPID().GetLibItemName())
    if n:
        n = save(pcbnew.Cast_to_FOOTPRINT(f.Duplicate(False)), n)
        f.SetFPID(pcbnew.LIB_ID("BADGE", saved[n.lower()]))
for n in pcbnew.FootprintEnumerate("../altium/chico.PcbLib"):
    if n.lower() in saved:
        continue
    fp = pcbnew.FootprintLoad("../altium/chico.PcbLib", n)
    save(fp, n)
b.Save("BADGE.kicad_pcb")
print(sorted(saved.values()))
