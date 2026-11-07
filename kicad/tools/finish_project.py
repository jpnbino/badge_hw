"""Patch BADGE.kicad_pro after conversion (text variables, hierarchy, ERC severities, drawing sheet)."""
import json, re, uuid
pro = json.load(open("BADGE.kicad_pro"))
root = re.search(r'\(uuid "([^"]+)"', open("BADGE.kicad_sch").read()).group(1)
pro["schematic"]["top_level_sheets"] = [{"filename": "BADGE.kicad_sch", "name": "BADGE", "uuid": root}]
pro["schematic"]["page_layout_descr_file"] = "BADGE.kicad_wks"
pro["sheets"] = [[root, "Root"]] + [
    [str(uuid.uuid5(uuid.NAMESPACE_URL, "badge-sheet-" + n)), n]
    for n in ["COVER PAGE", "BLOCK DIAGRAM", "CIRCUIT", "MECHANICAL", "REVISION HISTORY"]]
pro["text_variables"] = {"Project": "Fun Badge PIC12F", "VersionRevision": "V1I1", "EngineerName": "Bino",
                         "Currency": "USD", "Assembly_Variant": "Full"}
erc = pro.setdefault("erc", {"meta": {"version": 0}, "rule_severities": {}})
erc.setdefault("rule_severities", {}).update({"endpoint_off_grid": "ignore", "unconnected_wire_endpoint": "warning"})
json.dump(pro, open("BADGE.kicad_pro", "w"), indent=2)
