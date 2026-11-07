"""Post-import board tweaks (Altium rules -> KiCad board setup). Run with KiCad python from kicad/."""
import pcbnew
mm = pcbnew.FromMM
b = pcbnew.LoadBoard("BADGE.kicad_pcb")
for z in b.Zones():
    if z.IsTeardropArea():
        z.SetLocalClearance(0)
for f in b.GetFootprints():
    for fld in f.GetFields():
        if fld.GetName() not in ("Reference", "Value"):
            fld.SetVisible(False)
    if not f.GetReference():
        f.Reference().SetVisible(False)
        f.Value().SetVisible(False)
ds = b.GetDesignSettings()
ds.m_MinClearance = mm(0.2)          # Clearance 7.874 mil
ds.m_TrackMinWidth = mm(0.1524)      # Width min 6 mil
ds.m_CopperEdgeClearance = mm(0.2)
ds.m_HoleToHoleMin = mm(0.254)       # HoleToHole 10 mil
ds.m_HoleClearance = mm(0.15)
ds.m_MinThroughDrill = mm(0.0254)    # HoleSize min 1 mil
ds.m_ViasMinAnnulus = mm(0.05)
ds.m_ViasMinSize = mm(0.3)
ds.m_MinSilkClearance = mm(0.1)
b.Save("BADGE.kicad_pcb")
b = pcbnew.LoadBoard("BADGE.kicad_pcb")
pcbnew.ZONE_FILLER(b).Fill(b.Zones())
b.Save("BADGE.kicad_pcb")
