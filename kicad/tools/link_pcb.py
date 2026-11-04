"""Link board footprints to schematic symbols (UUID path), sync values/fields, and
rename PCB nets to the schematic net names. Run with KiCad python from kicad/ after
exporting the netlist: kicad-cli sch export netlist --format kicadxml -o n.xml BADGE.kicad_sch"""
import json, pcbnew
import xml.etree.ElementTree as ET
ref = json.load(open("tools/refmap.json"))
root = ET.parse("n.xml").getroot()
vals = {c.get("ref"): c for c in root.find("components")}
pin2net = {}
for n in root.find("nets"):
    for nd in n:
        pin2net[(nd.get("ref"), nd.get("pin"))] = n.get("name")
b = pcbnew.LoadBoard("BADGE.kicad_pcb")
for f in b.GetFootprints():
    r = f.GetReference()
    if r in ref:
        f.SetPath(pcbnew.KIID_PATH(ref[r]["path"]))
        c = vals[r]
        f.SetValue(c.find("value").text or "")

nets = {}
for f in b.GetFootprints():
    for p in f.Pads():
        k = (f.GetReference(), p.GetNumber())
        if k in pin2net and p.GetNetname():
            nets[p.GetNetname()] = pin2net[k]
for old, new in nets.items():
    if old != new:
        b.FindNet(old).SetNetname(new)
b.Save("BADGE.kicad_pcb")
print(len(nets), "nets mapped")
