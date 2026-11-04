"""Convert the frozen Altium schematic project (../altium/*.SchDoc, chico.SchLib) to KiCad.

Usage (from kicad/):  python3 tools/sch_convert.py
Produces BADGE.kicad_sch (root) + one .kicad_sch per sub sheet, BADGE.kicad_sym and sym-lib-table.
Footprint associations are taken from the already imported BADGE.kicad_pcb.
"""
import base64, glob, os, re, struct, sys, uuid, zlib, math, collections
import olefile

sys.path.insert(0, os.path.dirname(__file__))
from aparse import records

ALT = os.path.join(os.path.dirname(__file__), "..", "..", "altium")
U = 0.254          # mm per Altium schematic unit (10 mil)
PAGE_H = 297.0     # A3 height in mm
PROJECT = "BADGE"
LIB = "BADGE"
SHEETS = ["COVER PAGE", "BLOCK DIAGRAM", "CIRCUIT", "MECHANICAL", "REVISION HISTORY"]
PROJECT_VARS = {"Project": "Fun Badge PIC12F", "VersionRevision": "V1I1", "EngineerName": "Bino",
                "Currency": "USD", "Assembly_Variant": "Full"}

_uid = [0]


def new_uuid():
    _uid[0] += 1
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "badge-%d" % _uid[0]))


def val(r, k, default=0.0):
    v = r.get(k)
    if v is None or v == "":
        return default
    try:
        f = float(v)
    except ValueError:
        return default
    return f + float(r.get(k + "_FRAC", 0) or 0) / 100000.0


def gets(r, k, default=""):
    u = r.get("%UTF8%" + k)
    if u is not None:
        try:
            return u.encode("latin-1").decode("utf-8")
        except UnicodeError:
            pass
    return r.get(k, default)


def fnum(x):
    s = "%.4f" % x
    s = s.rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def esc(s):
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def overbar(s):
    """Altium 'M\\C\\L\\R\\' -> KiCad '~{MCLR}'."""
    if "\\" not in s:
        return s
    out, i, open_ = "", 0, False
    while i < len(s):
        c = s[i]
        bar = i + 1 < len(s) and s[i + 1] == "\\"
        if bar:
            if not open_:
                out += "~{"
                open_ = True
            out += c
            i += 2
        else:
            if open_:
                out += "}"
                open_ = False
            out += c
            i += 1
    if open_:
        out += "}"
    return out


def special(s):
    if not s.startswith("="):
        return s
    n = s[1:].lower()
    m = {"currentdate": "${CURRENT_DATE}", "currenttime": "${CURRENT_DATE}", "sheetnumber": "${#}",
         "sheettotal": "${##}", "documentname": "${FILENAME}", "date": "${CURRENT_DATE}"}
    if n in m:
        return m[n]
    for k in PROJECT_VARS:
        if k.lower() == n:
            return "${%s}" % k
    return s


def color(c):
    c = int(c or 0)
    return (c & 255, (c >> 8) & 255, (c >> 16) & 255)


def sanitize(name):
    return re.sub(r'[\\/:"<>|*?]', "_", name)


# ---------------------------------------------------------------- vector helpers
DIRS = [(1, 0), (0, 1), (-1, 0), (0, -1)]


def rot(p, o):
    x, y = p
    for _ in range(o % 4):
        x, y = -y, x
    return (x, y)


class Xf:
    """abs = R(o) M local + origin  (Altium component placement)."""

    def __init__(self, origin, o, mirror):
        self.origin, self.o, self.mirror = origin, o, mirror

    def to_local(self, p):
        x, y = p[0] - self.origin[0], p[1] - self.origin[1]
        # mirroring is baked into the "_M" library symbol, so only the rotation is undone
        return rot((x, y), -self.o)

    def dir_to_local(self, code):
        return (code - self.o) % 4


# ---------------------------------------------------------------- symbol building
ETYPE = {0: "input", 1: "bidirectional", 2: "output", 3: "open_collector", 4: "passive",
         5: "tri_state", 6: "open_emitter", 7: "power_in"}


def stroke(w=None):
    return "(stroke (width %s) (type default))" % fnum(w if w is not None else 0.1524)


def lw(r):
    return {0: 0.1, 1: 0.1524, 2: 0.254, 3: 0.4}.get(int(r.get("LINEWIDTH", 1) or 1), 0.1524)


def fill_of(r):
    return "(fill (type background))" if r.get("ISSOLID") == "T" else "(fill (type none))"


def sym_shapes(children, xf):
    """Return (graphics_lines, pins[]) for a component, in local lib coordinates (mm, y up)."""
    gfx, pins = [], []

    def P(p):
        l = xf.to_local(p)
        return (l[0] * U, l[1] * U)

    def pt(p):
        q = P(p)
        return "(xy %s %s)" % (fnum(q[0]), fnum(q[1]))

    for r in children:
        t = r.get("RECORD")
        a = (val(r, "LOCATION.X"), val(r, "LOCATION.Y"))
        if t == "13":
            b = (val(r, "CORNER.X"), val(r, "CORNER.Y"))
            gfx.append("(polyline (pts %s %s) %s (fill (type none)))" % (pt(a), pt(b), stroke(lw(r))))
        elif t == "6" or t == "7":
            n = int(r.get("LOCATIONCOUNT", 0))
            ps = [(val(r, "X%d" % i), val(r, "Y%d" % i)) for i in range(1, n + 1)]
            if t == "7" and ps:
                ps.append(ps[0])
            f = fill_of(r) if t == "7" else "(fill (type none))"
            gfx.append("(polyline (pts %s) %s %s)" % (" ".join(pt(p) for p in ps), stroke(lw(r)), f))
        elif t in ("14", "10"):
            b = (val(r, "CORNER.X"), val(r, "CORNER.Y"))
            p1, p2 = P(a), P(b)
            gfx.append("(rectangle (start %s %s) (end %s %s) %s %s)" % (
                fnum(p1[0]), fnum(p1[1]), fnum(p2[0]), fnum(p2[1]), stroke(lw(r)), fill_of(r)))
        elif t in ("12", "11", "8"):
            rad = val(r, "RADIUS") + val(r, "RADIUS_FRAC") * 0
            sa = val(r, "STARTANGLE", 0.0)
            ea = val(r, "ENDANGLE", 360.0)
            if t == "8":
                rad = val(r, "RADIUS")
                sa, ea = 0, 360
            if t == "11":
                rad = val(r, "RADIUS")
            full = abs((ea - sa) % 360) < 1e-6
            c = P(a)
            if full:
                gfx.append("(circle (center %s %s) (radius %s) %s %s)" % (
                    fnum(c[0]), fnum(c[1]), fnum(rad * U), stroke(lw(r)),
                    fill_of(r) if t == "8" else "(fill (type none))"))
            else:
                if ea < sa:
                    ea += 360
                pts = []
                for ang in (sa, (sa + ea) / 2, ea):
                    ar = math.radians(ang)
                    pts.append((a[0] + rad * math.cos(ar), a[1] + rad * math.sin(ar)))
                s, m, e = (P(p) for p in pts)
                gfx.append("(arc (start %s %s) (mid %s %s) (end %s %s) %s (fill (type none)))" % (
                    fnum(s[0]), fnum(s[1]), fnum(m[0]), fnum(m[1]), fnum(e[0]), fnum(e[1]), stroke(lw(r))))
        elif t == "4":
            txt = overbar(gets(r, "TEXT"))
            p = P(a)
            gfx.append('(text "%s" (at %s %s 0) (effects (font (size 1.27 1.27)) (justify left bottom)))' % (
                esc(txt), fnum(p[0]), fnum(p[1])))
        elif t == "2":
            conglom = int(r.get("PINCONGLOMERATE", 0))
            # Altium: location is the body end, rotation points outwards to the connection end
            ln_u = val(r, "PINLENGTH")
            d = DIRS[conglom & 3]
            code = (xf.dir_to_local(conglom & 3) + 2) % 4
            ln = ln_u * U
            p = P((a[0] + d[0] * ln_u, a[1] + d[1] * ln_u))
            name = overbar(gets(r, "NAME"))
            num = gets(r, "DESIGNATOR")
            et = ETYPE.get(int(r.get("ELECTRICAL", 4)), "passive")
            pins.append(dict(type=et, x=p[0], y=p[1], ang=code * 90, len=ln, name=name, num=num,
                             show_name=bool(conglom & 0x10) and name != "",
                             show_num=bool(conglom & 0x20), hide=False))
    return gfx, pins


def render_symbol(name, gfx, pins, props, power=False, embed_prefix=""):
    ps = []
    for k, v, hide in props:
        ps.append('(property "%s" "%s" (at 0 0 0) (effects (font (size 1.27 1.27))%s))' % (
            k, esc(v), " hide" if hide else ""))
    out = ['(symbol "%s%s" %s(exclude_from_sim no) (in_bom %s) (on_board %s)' % (
        embed_prefix, name, "(power) " if power else "", "no" if power else "yes", "no" if power else "yes")]
    out.append("(pin_numbers (hide yes))" if power else "")
    out.append("(pin_names (offset 0.508))")
    out += ps
    out.append('(symbol "%s_0_1" %s)' % (name, " ".join(gfx)))
    pl = []
    for p in pins:
        nm = '(name "%s" (effects (font (size 1.27 1.27))%s))' % (esc(p["name"] or "~"), "" if p["show_name"] else " hide")
        nu = '(number "%s" (effects (font (size 1.27 1.27))%s))' % (esc(p["num"]), "" if p["show_num"] else " hide")
        pl.append("(pin %s line (at %s %s %d) (length %s)%s %s %s)" % (
            p["type"], fnum(p["x"]), fnum(p["y"]), p["ang"], fnum(p["len"]), " hide" if p["hide"] else "", nm, nu))
    out.append('(symbol "%s_1_1" %s)' % (name, " ".join(pl)))
    out.append("(embedded_fonts no))")
    return "\n".join(o for o in out if o)


# ---------------------------------------------------------------- footprint map from PCB
def fp_map():
    m = {}
    txt = open(os.path.join(os.path.dirname(__file__), "..", "BADGE.kicad_pcb"), encoding="utf-8").read()
    for fm in re.finditer(r'\(footprint "([^"]+)"', txt):
        seg = txt[fm.end(): fm.end() + 3000]
        rm = re.search(r'\(property "Reference" "([^"]*)"', seg)
        if rm and rm.group(1):
            m[rm.group(1)] = fm.group(1)
    return m


# ---------------------------------------------------------------- sheet conversion
class Lib:
    def __init__(self):
        self.syms = collections.OrderedDict()     # name -> definition text (name unprefixed internally)
        self.power = {}

    def add(self, name, text):
        self.syms.setdefault(name, text)


def power_symbol(lib, net, style):
    key = (net, style)
    if key in lib.power:
        return lib.power[key]
    base = "PWR_" + sanitize(net)
    name = base if base not in [v for v in lib.power.values()] else "%s_s%d" % (base, style)
    if name in lib.power.values():
        name = "%s_s%d" % (base, style)
    lib.power[key] = name
    g = ["(polyline (pts (xy 0 0) (xy 0 2.54)) %s (fill (type none)))" % stroke()]
    if style in (4, 5, 6):
        g += ["(polyline (pts (xy -1.27 2.54) (xy 1.27 2.54)) %s (fill (type none)))" % stroke(),
              "(polyline (pts (xy -0.762 3.048) (xy 0.762 3.048)) %s (fill (type none)))" % stroke(),
              "(polyline (pts (xy -0.254 3.556) (xy 0.254 3.556)) %s (fill (type none)))" % stroke()]
    elif style == 0:
        g.append("(circle (center 0 3.175) (radius 0.635) %s (fill (type none)))" % stroke())
    elif style == 1:
        g.append("(polyline (pts (xy -0.762 2.54) (xy 0 3.556) (xy 0.762 2.54) (xy -0.762 2.54)) %s (fill (type outline)))" % stroke())
    else:
        g.append("(polyline (pts (xy -1.27 2.54) (xy 1.27 2.54)) %s (fill (type none)))" % stroke())
    pin = [dict(type="power_in", x=0, y=0, ang=90, len=0, name=net, num="1", show_name=False, show_num=False, hide=True)]
    props = [("Reference", "#PWR", True), ("Value", net, False), ("Footprint", "", True),
             ("Datasheet", "", True), ("Description", "Power symbol (converted from Altium power port)", True)]
    lib.add(name, render_symbol(name, g, pin, props, power=True))
    return name


def sheet_uuid(base):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "badge-sheet-" + base))


def xy(p):
    return (p[0] * U, PAGE_H - p[1] * U)


def font_of(sheet_hdr, fid):
    try:
        i = int(fid)
    except (TypeError, ValueError):
        i = 1
    size = float(sheet_hdr.get("SIZE%d" % i, 10))
    return (max(size * 0.2, 1.0), sheet_hdr.get("BOLD%d" % i) == "T", sheet_hdr.get("ITALIC%d" % i) == "T")


def effects(size, bold=False, italic=False, rgb=None, justify="", hide=False):
    f = "(font (size %s %s)%s%s%s)" % (fnum(size), fnum(size), " bold" if bold else "", " italic" if italic else "",
                                      " (color %d %d %d 1)" % rgb if rgb and rgb != (0, 0, 0) else "")
    return "(effects %s%s%s)" % (f, " (justify %s)" % justify if justify else "", " hide" if hide else "")


def png_from_bmp(d):
    off = struct.unpack("<I", d[10:14])[0]
    w, h, _, bpp = struct.unpack("<iiHH", d[18:30])
    rows = []
    stride = ((w * bpp + 31) // 32) * 4
    for y in range(abs(h)):
        row = d[off + y * stride: off + y * stride + w * bpp // 8]
        if bpp == 24:
            px = b"".join(row[i + 2: i + 3] + row[i + 1: i + 2] + row[i: i + 1] for i in range(0, len(row), 3))
        elif bpp == 32:
            px = b"".join(row[i + 2: i + 3] + row[i + 1: i + 2] + row[i: i + 1] for i in range(0, len(row), 4))
        else:
            return None
        rows.append(b"\x00" + px)
    if h > 0:
        rows.reverse()
    raw = zlib.compress(b"".join(rows), 9)

    def chunk(t, c):
        return struct.pack(">I", len(c)) + t + c + struct.pack(">I", zlib.crc32(t + c) & 0xFFFFFFFF)

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, abs(h), 8, 2, 0, 0, 0)) + \
        chunk(b"IDAT", raw) + chunk(b"IEND", b"")


def storage_images(path):
    o = olefile.OleFileIO(path)
    if not o.exists("Storage"):
        return []
    d = o.openstream("Storage").read()
    n = struct.unpack("<I", d[:4])[0] & 0xFFFFFF
    p, out = 4 + n, []
    while p + 4 <= len(d):
        sz = struct.unpack("<I", d[p: p + 4])[0] & 0xFFFFFF
        body = d[p + 4: p + 4 + sz]
        p += 4 + sz
        if len(body) > 2 and body[0] == 0xD0:
            ln = body[1]
            blob = body[2 + ln:]
            for off in range(0, 8):
                try:
                    out.append(zlib.decompress(blob[off:]))
                    break
                except zlib.error:
                    continue
    return out


def convert_sheet(path, lib, fps, root_uuid, sheet_path_uuid, pageno, refcount, is_root=False):
    recs = records(path)
    hdr = recs[0]
    body = recs[1:]          # OWNERINDEX is relative to this list
    out = []
    items = []
    comps = {i: r for i, r in enumerate(body) if r.get("RECORD") == "1"}
    children = collections.defaultdict(list)
    for i, r in enumerate(body):
        oi = r.get("OWNERINDEX")
        if oi is not None and int(oi) in comps:
            children[int(oi)].append(r)
    owned_by_comp = {id(c) for cl in children.values() for c in cl}
    # descendants (implementations) are owned by non-component records; ignore them
    docparams = {r.get("NAME"): gets(r, "TEXT") for r in body if r.get("RECORD") == "41" and "OWNERINDEX" not in r}
    images = None
    instances = []   # (reference, ...) for refcount bookkeeping

    def txt_size(r):
        return font_of(hdr, r.get("FONTID"))

    inst_path = "/" + root_uuid + (("/" + sheet_path_uuid) if sheet_path_uuid else "")
    sheet_syms = []
    sheet_children = []

    for i, r in enumerate(body):
        t = r.get("RECORD")
        oi = r.get("OWNERINDEX")
        # component instances
        if t == "1":
            kids = children[i]
            origin = (val(r, "LOCATION.X"), val(r, "LOCATION.Y"))
            o = int(r.get("ORIENTATION", 0) or 0)
            mir = r.get("ISMIRRORED") == "T"
            xf = Xf(origin, o, mir)
            libref = gets(r, "LIBREFERENCE") or gets(r, "DESIGNITEMID")
            symname = sanitize(libref) + ("_M" if mir else "")
            params = {}
            desig, desig_pos = "U?", origin
            model = None
            for k in kids:
                kt = k.get("RECORD")
                if kt == "34":
                    desig = gets(k, "TEXT")
                    desig_pos = (val(k, "LOCATION.X"), val(k, "LOCATION.Y"))
                    desig_o = int(k.get("ORIENTATION", 0) or 0)
                elif kt == "41":
                    params[gets(k, "NAME")] = (gets(k, "TEXT"), k.get("ISHIDDEN") == "T",
                                               (val(k, "LOCATION.X"), val(k, "LOCATION.Y")))
            for j, k in enumerate(body):
                if k.get("RECORD") == "45" and k.get("MODELTYPE") == "PCBLIB" and k.get("ISCURRENT") == "T":
                    own = body[int(k["OWNERINDEX"])]
                    if own.get("RECORD") == "44" and int(own.get("OWNERINDEX", -1)) == i:
                        model = k.get("MODELNAME")
            gfx, pins = sym_shapes(kids, xf)
            value = params.get("Value", (None,))[0] or params.get("Comment", (None,))[0] or libref
            desc = gets(r, "COMPONENTDESCRIPTION")
            fp = fps.get(desig, "")
            lprops = [("Reference", desig[:1] if desig[:1].isalpha() else "U", False), ("Value", libref, False),
                      ("Footprint", fp, True), ("Datasheet", "~", True), ("Description", desc, True)]
            lib.add(symname, render_symbol(symname, gfx, pins, lprops))
            vis_ref = (desig_pos)
            props = []
            rx, ry = xy(desig_pos)
            props.append('(property "Reference" "%s" (at %s %s 0) %s)' % (esc(desig), fnum(rx), fnum(ry),
                                                                         effects(1.27, justify="left bottom")))
            vx, vy = xy(params["Value"][2]) if "Value" in params else xy(origin)
            vhide = params["Value"][1] if "Value" in params else False
            props.append('(property "Value" "%s" (at %s %s 0) %s)' % (esc(value), fnum(vx), fnum(vy),
                                                                      effects(1.27, justify="left bottom", hide=vhide)))
            props.append('(property "Footprint" "%s" (at %s %s 0) %s)' % (esc(fp), fnum(xy(origin)[0]), fnum(xy(origin)[1]),
                                                                           effects(1.27, hide=True)))
            props.append('(property "Datasheet" "~" (at %s %s 0) %s)' % (fnum(xy(origin)[0]), fnum(xy(origin)[1]),
                                                                          effects(1.27, hide=True)))
            props.append('(property "Description" "%s" (at %s %s 0) %s)' % (esc(desc), fnum(xy(origin)[0]), fnum(xy(origin)[1]),
                                                                             effects(1.27, hide=True)))
            if model:
                props.append('(property "Altium_Model" "%s" (at %s %s 0) %s)' % (esc(model), fnum(xy(origin)[0]), fnum(xy(origin)[1]),
                                                                                   effects(1.27, hide=True)))
            for k, (tv, hid, pos) in params.items():
                if k in ("Value",):
                    continue
                kk = ("Altium_" + k) if k in ("Reference", "Footprint", "Datasheet", "Description") else k
                px, py = xy(pos)
                props.append('(property "%s" "%s" (at %s %s 0) %s)' % (esc(kk), esc(tv), fnum(px), fnum(py),
                                                                       effects(1.27, hide=True)))
            ox, oy = xy(origin)
            pinuuids = "\n".join('(pin "%s" (uuid "%s"))' % (esc(p["num"]), new_uuid()) for p in pins)
            refcount[0] += 1
            symuuid = new_uuid()
            REFMAP[desig] = {"path": "%s/%s" % (inst_path, symuuid), "value": value}
            items.append(
                '(symbol (lib_id "%s:%s") (at %s %s %d) (unit 1) (exclude_from_sim no) (in_bom %s) (on_board %s) (dnp no)\n'
                '(uuid "%s")\n%s\n%s\n(instances (project "%s" (path "%s" (reference "%s") (unit 1)))))' % (
                    LIB, symname, fnum(ox), fnum(oy), o * 90, "yes" if fp else "no", "yes" if fp else "no", symuuid, "\n".join(props), pinuuids, PROJECT, inst_path,
                    esc(desig)))
            continue
        if id(r) in owned_by_comp:
            continue
        if oi is not None and body[int(oi)].get("RECORD") not in ("31", "39", "15"):
            # template / sheet-symbol owned children other than graphics handled below
            if body[int(oi)].get("RECORD") not in ("39",):
                continue
        # ---- sheet-level primitives
        col = color(r.get("COLOR"))
        if t == "27":
            n = int(r.get("LOCATIONCOUNT", 0))
            ps = [xy((val(r, "X%d" % k), val(r, "Y%d" % k))) for k in range(1, n + 1)]
            for a, b in zip(ps, ps[1:]):
                items.append('(wire (pts (xy %s %s) (xy %s %s)) %s (uuid "%s"))' % (
                    fnum(a[0]), fnum(a[1]), fnum(b[0]), fnum(b[1]), stroke(0), new_uuid()))
        elif t == "29":
            p = xy((val(r, "LOCATION.X"), val(r, "LOCATION.Y")))
            items.append('(junction (at %s %s) (diameter 0) (color 0 0 0 0) (uuid "%s"))' % (fnum(p[0]), fnum(p[1]), new_uuid()))
        elif t == "22":
            p = xy((val(r, "LOCATION.X"), val(r, "LOCATION.Y")))
            items.append('(no_connect (at %s %s) (uuid "%s"))' % (fnum(p[0]), fnum(p[1]), new_uuid()))
        elif t == "25":
            p = xy((val(r, "LOCATION.X"), val(r, "LOCATION.Y")))
            ang = {0: 0, 1: 90, 2: 180, 3: 270}[int(r.get("ORIENTATION", 0) or 0)]
            items.append('(label "%s" (at %s %s %d) %s (uuid "%s"))' % (
                esc(overbar(gets(r, "TEXT"))), fnum(p[0]), fnum(p[1]), ang,
                effects(1.27, justify="left bottom" if ang in (0, 90) else "right bottom"), new_uuid()))
        elif t == "17":
            net = gets(r, "TEXT")
            style = int(r.get("STYLE", 2) or 2)
            name = power_symbol(lib, net, style)
            o = int(r.get("ORIENTATION", 0) or 0)
            p = xy((val(r, "LOCATION.X"), val(r, "LOCATION.Y")))
            ang = ((o - 1) * 90) % 360
            refcount[1] += 1
            ref = "#PWR%03d" % refcount[1]
            items.append(
                '(symbol (lib_id "%s:%s") (at %s %s %d) (unit 1) (exclude_from_sim no) (in_bom no) (on_board no) (dnp no)\n'
                '(uuid "%s")\n(property "Reference" "%s" (at %s %s 0) %s)\n(property "Value" "%s" (at %s %s 0) %s)\n'
                '(property "Footprint" "" (at %s %s 0) %s)\n(property "Datasheet" "" (at %s %s 0) %s)\n'
                '(pin "1" (uuid "%s"))\n(instances (project "%s" (path "%s" (reference "%s") (unit 1)))))' % (
                    LIB, name, fnum(p[0]), fnum(p[1]), ang, new_uuid(), ref, fnum(p[0]), fnum(p[1]), effects(1.27, hide=True),
                    esc(net), fnum(p[0]), fnum(p[1]), effects(1.27, hide=r.get("SHOWNETNAME") != "T"),
                    fnum(p[0]), fnum(p[1]), effects(1.27, hide=True), fnum(p[0]), fnum(p[1]), effects(1.27, hide=True),
                    new_uuid(), PROJECT, inst_path, ref))
        elif t == "4":
            text = special(gets(r, "TEXT"))
            if not text:
                continue
            p = xy((val(r, "LOCATION.X"), val(r, "LOCATION.Y")))
            o = int(r.get("ORIENTATION", 0) or 0)
            sz, b, it = txt_size(r)
            just = {0: "left bottom", 1: "left bottom", 2: "right bottom", 3: "right bottom"}[o]
            items.append('(text "%s" (exclude_from_sim no) (at %s %s %d) %s (uuid "%s"))' % (
                esc(text.replace("~1", "\n")), fnum(p[0]), fnum(p[1]), o * 90 % 360,
                effects(sz, b, it, col, just), new_uuid()))
        elif t == "28":
            text = special(gets(r, "TEXT")).replace("~1", "\n")
            a = (val(r, "LOCATION.X"), val(r, "LOCATION.Y"))
            c = (val(r, "CORNER.X"), val(r, "CORNER.Y"))
            p = xy((a[0], c[1]))
            sz, b, it = txt_size(r)
            rgb = color(r.get("TEXTCOLOR", 0))
            items.append('(text_box "%s" (exclude_from_sim no) (at %s %s 0) (size %s %s) (margins 0.5 0.5 0.5 0.5) %s (fill (type none)) %s (uuid "%s"))' % (
                esc(text), fnum(p[0]), fnum(p[1]), fnum((c[0] - a[0]) * U), fnum((c[1] - a[1]) * U),
                "(stroke (width 0.1524) (type solid))" if r.get("ISSOLID") == "T" or r.get("SHOWBORDER") == "T" else "(stroke (width 0) (type solid) (color 0 0 0 0))",
                effects(sz, b, it, rgb, "left top"), new_uuid()))
        elif t == "13":
            a = xy((val(r, "LOCATION.X"), val(r, "LOCATION.Y")))
            b = xy((val(r, "CORNER.X"), val(r, "CORNER.Y")))
            items.append('(polyline (pts (xy %s %s) (xy %s %s)) (stroke (width %s) (type default) (color %d %d %d 1)) (uuid "%s"))' % (
                fnum(a[0]), fnum(a[1]), fnum(b[0]), fnum(b[1]), fnum(lw(r)), col[0], col[1], col[2], new_uuid()))
        elif t in ("6", "7"):
            n = int(r.get("LOCATIONCOUNT", 0))
            ps = [xy((val(r, "X%d" % k), val(r, "Y%d" % k))) for k in range(1, n + 1)]
            if t == "7" and ps:
                ps.append(ps[0])
            items.append('(polyline (pts %s) (stroke (width %s) (type default) (color %d %d %d 1)) (uuid "%s"))' % (
                " ".join("(xy %s %s)" % (fnum(p[0]), fnum(p[1])) for p in ps), fnum(lw(r)), col[0], col[1], col[2], new_uuid()))
        elif t in ("14", "10"):
            a = xy((val(r, "LOCATION.X"), val(r, "LOCATION.Y")))
            b = xy((val(r, "CORNER.X"), val(r, "CORNER.Y")))
            ac = color(r.get("AREACOLOR", 16777215))
            filled = r.get("ISSOLID") == "T" and r.get("TRANSPARENT") != "T"
            fill = "(fill (type color) (color %d %d %d 1))" % ac if filled else "(fill (type none))"
            items.append('(rectangle (start %s %s) (end %s %s) (stroke (width %s) (type default) (color %d %d %d 1)) %s (uuid "%s"))' % (
                fnum(a[0]), fnum(a[1]), fnum(b[0]), fnum(b[1]), fnum(lw(r)), col[0], col[1], col[2], fill, new_uuid()))
        elif t == "30":
            if images is None:
                images = storage_images(path)
            if images:
                png = png_from_bmp(images[0]) if images[0][:2] == b"BM" else images[0]
                if png:
                    a = (val(r, "LOCATION.X"), val(r, "LOCATION.Y"))
                    c = (val(r, "CORNER.X"), val(r, "CORNER.Y"))
                    w, h = struct.unpack(">II", png[16:24])
                    scale = (c[0] - a[0]) * U / (w * 25.4 / 300.0)
                    cx = xy(((a[0] + c[0]) / 2, (a[1] + c[1]) / 2))
                    b64 = base64.b64encode(png).decode()
                    chunks = "\n".join(b64[k:k + 76] for k in range(0, len(b64), 76))
                    items.append('(image (at %s %s) (scale %s) (uuid "%s") (data "%s"))' % (
                        fnum(cx[0]), fnum(cx[1]), fnum(scale), new_uuid(), chunks.replace("\n", '" "')))
        elif t == "15":
            sx, sy = val(r, "LOCATION.X"), val(r, "LOCATION.Y")
            w, h = val(r, "XSIZE"), val(r, "YSIZE")
            fname = None
            for k, rr in enumerate(body):
                if rr.get("RECORD") == "33" and int(rr.get("OWNERINDEX", -1)) == i:
                    fname = gets(rr, "TEXT")
            sheet_syms.append((sx, sy, w, h, fname))

    # sheet symbols (root)
    sheet_uuids = {}
    for (sx, sy, w, h, fname) in sheet_syms:
        base = os.path.splitext(fname)[0]
        p = xy((sx, sy))
        su = sheet_uuid(base)
        sheet_uuids[base] = su
        pg = SHEETS.index(base) + 2 if base in SHEETS else 99
        items.append(
            '(sheet (at %s %s) (size %s %s) (exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no)\n'
            '(stroke (width 0.1524) (type solid)) (fill (color 255 255 194 1))\n(uuid "%s")\n'
            '(property "Sheetname" "%s" (at %s %s 0) %s)\n(property "Sheetfile" "%s.kicad_sch" (at %s %s 0) %s)\n'
            '(instances (project "%s" (path "/%s" (page "%d")))))' % (
                fnum(p[0]), fnum(p[1]), fnum(w * U), fnum(h * U), su, esc(base), fnum(p[0]), fnum(p[1] - 0.8),
                effects(1.27, justify="left bottom"), esc(base), fnum(p[0]), fnum(p[1] + h * U + 1.5),
                effects(1.27, justify="left top"), PROJECT, root_uuid, pg))
    return items, sheet_uuids, docparams


def write_sheet(fname, uuid_, items, lib, paper="A3"):
    libs = "\n".join(re.sub(r'^\(symbol "', '(symbol "%s:' % LIB, v, count=1) for v in lib.syms.values()
                      if v.split('"')[1] in used_names(items))
    open(fname, "w", encoding="utf-8").write(
        '(kicad_sch (version 20250114) (generator "eeschema") (generator_version "9.0")\n(uuid "%s")\n(paper "%s")\n'
        '(title_block (title "%s") (date "") (rev "%s") (company "KDS") (comment 1 "Converted from Altium Designer"))\n'
        '(lib_symbols\n%s\n)\n%s\n(sheet_instances (path "/" (page "1")))\n(embedded_fonts no))\n' % (
            uuid_, paper, os.path.splitext(os.path.basename(fname))[0], PROJECT_VARS["VersionRevision"], libs, "\n".join(items)))


def used_names(items):
    s = set()
    for it in items:
        for m in re.finditer(r'\(lib_id "%s:([^"]+)"' % LIB, it):
            s.add(m.group(1))
    return s


def schlib_symbols(lib):
    path = os.path.join(ALT, "chico.SchLib")
    o = olefile.OleFileIO(path)
    names = [d[0] for d in o.listdir() if len(d) == 2 and d[1] == "Data"]
    for n in names:
        recs = records(path, n + "/Data")
        comp = recs[0]
        libref = gets(comp, "LIBREFERENCE") or n
        name = sanitize(libref)
        if name in lib.syms:
            continue
        kids = recs[1:]
        xf = Xf((val(comp, "LOCATION.X"), val(comp, "LOCATION.Y")), 0, False)
        gfx, pins = sym_shapes([k for k in kids if k.get("RECORD") not in ("1",)], xf)
        desc = gets(comp, "COMPONENTDESCRIPTION")
        lib.add(name, render_symbol(name, gfx, pins, [("Reference", "U", False), ("Value", libref, False),
                                                       ("Footprint", "", True), ("Datasheet", "~", True),
                                                       ("Description", desc, True)]))


def main():
    os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    fps = fp_map()
    lib = Lib()
    root_uuid = new_uuid()
    refcount = [0, 0]
    for idx, name in enumerate(SHEETS):
        its, _, _ = convert_sheet(os.path.join(ALT, name + ".SchDoc"), lib, fps, root_uuid, sheet_uuid(name), idx + 2, refcount)
        write_sheet(name + ".kicad_sch", new_uuid(), its, lib)
    items, _, _ = convert_sheet(os.path.join(ALT, "BADGE.SchDoc"), lib, fps, root_uuid, None, 1, refcount, True)
    write_sheet("BADGE.kicad_sch", root_uuid, items, lib)
    lib2 = lib
    schlib_symbols(lib2)
    # symbol library
    with open("BADGE.kicad_sym", "w", encoding="utf-8") as f:
        f.write('(kicad_symbol_lib (version 20250114) (generator "kicad_symbol_editor") (generator_version "9.0")\n')
        f.write("\n".join(lib2.syms.values()))
        f.write("\n)\n")
    import json
    json.dump(REFMAP, open("tools/refmap.json", "w"), indent=1, sort_keys=True)
    print("symbols:", len(lib2.syms), "components:", len(REFMAP))


SHEET_ITEMS = {}
REFMAP = {}

if __name__ == "__main__":
    main()
