#!/bin/sh
# Regenerate everything in kicad/outputs/ (run from anywhere).
set -e
cd "$(dirname "$0")/.."
K=${KICAD_CLI:-/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli}
O=outputs
rm -rf $O && mkdir -p $O/gerbers $O/renders
$K sch export pdf -o $O/BADGE_schematic.pdf BADGE.kicad_sch
$K sch export bom --fields 'Reference,Value,Footprint,${QUANTITY},Manufacturer,Manufacturer Part Number' -o $O/BADGE_BOM.csv BADGE.kicad_sch
$K sch export netlist --format kicadsexpr -o $O/BADGE.net BADGE.kicad_sch
$K pcb export gerbers --board-plot-params -o $O/gerbers/ BADGE.kicad_pcb
$K pcb export drill --format excellon --generate-map --map-format pdf -o $O/gerbers/ BADGE.kicad_pcb
$K pcb export pos --format csv --units mm -o $O/BADGE_pick_and_place.csv BADGE.kicad_pcb
$K pcb export step --subst-models -o $O/BADGE.step BADGE.kicad_pcb
$K pcb render --side top --quality high -o $O/renders/BADGE_top.png BADGE.kicad_pcb
$K pcb render --side bottom --quality high -o $O/renders/BADGE_bottom.png BADGE.kicad_pcb
$K pcb export pdf --layers F.Cu,F.Silkscreen,Edge.Cuts -o $O/BADGE_top_layer.pdf BADGE.kicad_pcb
$K pcb export pdf --layers B.Cu,B.Silkscreen,Edge.Cuts --mirror -o $O/BADGE_bottom_layer.pdf BADGE.kicad_pcb
$K sch erc --severity-all -o $O/BADGE_erc.rpt BADGE.kicad_sch || true
$K pcb drc --all-track-errors --schematic-parity --severity-all -o $O/BADGE_drc.rpt BADGE.kicad_pcb || true
