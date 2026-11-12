#!/usr/bin/env python3
"""
Carrier PCB for the air-quality node - generator, design-rule check and Gerber export.

    ESP32-DevKitC (38 pins)  plugs into two 1x19 sockets
    GY-BME680 (6 pins)       plugs into a 1x6 socket   } isolated behind a milled
    SCD41 module (4 pins)    plugs into a 1x4 socket   } thermal slot
    PMS7003                  lies on the board, 5 wires from its adapter to J4

Two-layer, through-hole only, 94 x 64 mm. No copper pour: the sensors must not
be heated through the board. Nothing but the bottom layer runs under the
PMS7003 metal can.

    python3 carrier.py            # DRC + gerbers/ + carrier-gerbers.zip
    python3 carrier.py --preview  # also renders preview.png (needs matplotlib)

Units: mm. Origin: bottom-left board corner, seen from the top (component) side.
The USB connector of the ESP32 sits at the Y = 0 edge.
"""
import math
import sys
import zipfile
from pathlib import Path

W, H = 94.0, 64.0
TOP, BOT = "F", "B"
PITCH = 2.54

# --- Trace classes -------------------------------------------------------------
W_PWR, W_SIG, W_SILK = 0.8, 0.4, 0.15
CLEARANCE = 0.30          # required copper-to-copper gap between different nets
EDGE_CLEARANCE = 0.50

# --- Placement -----------------------------------------------------------------
ESP_X = 64.7              # electrical row (3V3 ... 5V); it faces the sensors
ESP_ROW_GAPS = (25.4, 22.86)  # DevKitC V4 = 25.4 mm; narrow clones = 22.86 mm
ESP_Y19 = 4.0             # pin 19 (USB end); pin 1 is 18 pitches further up
J_SENS_X = 3.0            # sensor sockets, pins running along Y
BME_Y0, SCD_Y0 = 40.0, 16.0
PMS_Y, PMS_X0 = 4.0, 26.0  # J4: wires to the PMS7003 adapter board
SLOT = (21.0, 22.6, 8.0, 56.0)        # milled thermal slot x0, x1, y0, y1
PMS_KEEPOUT = (23.0, 60.6, 15.6, H)   # PMS7003 footprint: bottom-layer copper only
MOUNT_HOLES = [(3.5, 3.5), (3.5, 60.5), (76.5, 60.5), (77.4, 3.5)]

ESP_J2 = ["3V3", "EN", "VP", "VN", "IO34", "IO35", "IO32", "IO33", "IO25", "IO26", "IO27",
          "IO14", "IO12", "GND", "IO13", "SD2", "SD3", "CMD", "5V"]
USED = {"3V3": "3V3", "GND": "GND", "5V": "5V", "IO32": "SCL", "IO33": "SDA",
        "IO25": "PMS_TX", "IO26": "PMS_RX", "IO27": "PMS_SET"}
BME_PINS = ["VCC", "GND", "SCL", "SDA", "SDO", "CS"]
SCD_PINS = ["VDD", "GND", "SCL", "SDA"]
PMS_PINS = ["5V", "GND", "TX", "RX", "SET"]
PIN_NET = {"VCC": "3V3", "VDD": "3V3", "GND": "GND", "SCL": "SCL", "SDA": "SDA",
           "5V": "5V", "TX": "PMS_TX", "RX": "PMS_RX", "SET": "PMS_SET"}

pads, vias, segs, silk, npth = [], [], [], [], []
_nc = [0]


def pad(ref, x, y, net=None, dia=1.8, drill=1.0):
    if net is None:
        _nc[0] += 1
        net = f"NC{_nc[0]}"
    pads.append(dict(ref=ref, x=x, y=y, net=net, dia=dia, drill=drill))
    return (x, y)


def via(x, y, net):
    vias.append(dict(x=x, y=y, net=net, dia=0.8, drill=0.4))


def track(layer, net, pts, width):
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        segs.append(dict(layer=layer, net=net, x1=x1, y1=y1, x2=x2, y2=y2, w=width))


# --- Stroke font (4 x 6 grid) for the silkscreen ------------------------------------
_O = [(1, 0), (0, 1), (0, 5), (1, 6), (3, 6), (4, 5), (4, 1), (3, 0), (1, 0)]
_P = [(0, 0), (0, 6), (3, 6), (4, 5), (4, 4), (3, 3), (0, 3)]
FONT = {
    "A": [[(0, 0), (0, 4), (2, 6), (4, 4), (4, 0)], [(0, 2), (4, 2)]],
    "B": [_P, [(3, 3), (4, 2), (4, 1), (3, 0), (0, 0)]],
    "C": [[(4, 1), (3, 0), (1, 0), (0, 1), (0, 5), (1, 6), (3, 6), (4, 5)]],
    "D": [[(0, 0), (0, 6), (2, 6), (4, 4), (4, 2), (2, 0), (0, 0)]],
    "E": [[(4, 0), (0, 0), (0, 6), (4, 6)], [(0, 3), (3, 3)]],
    "F": [[(0, 0), (0, 6), (4, 6)], [(0, 3), (3, 3)]],
    "G": [[(4, 5), (3, 6), (1, 6), (0, 5), (0, 1), (1, 0), (3, 0), (4, 1), (4, 3), (2, 3)]],
    "H": [[(0, 0), (0, 6)], [(4, 0), (4, 6)], [(0, 3), (4, 3)]],
    "I": [[(2, 0), (2, 6)], [(1, 0), (3, 0)], [(1, 6), (3, 6)]],
    "J": [[(0, 1), (1, 0), (3, 0), (4, 1), (4, 6)]],
    "K": [[(0, 0), (0, 6)], [(4, 6), (0, 3), (4, 0)]],
    "L": [[(0, 6), (0, 0), (4, 0)]],
    "M": [[(0, 0), (0, 6), (2, 3), (4, 6), (4, 0)]],
    "N": [[(0, 0), (0, 6), (4, 0), (4, 6)]],
    "O": [_O],
    "P": [_P],
    "Q": [_O, [(2, 2), (4, 0)]],
    "R": [_P, [(2, 3), (4, 0)]],
    "S": [[(4, 5), (3, 6), (1, 6), (0, 5), (0, 4), (1, 3), (3, 3), (4, 2), (4, 1), (3, 0), (1, 0), (0, 1)]],
    "T": [[(2, 0), (2, 6)], [(0, 6), (4, 6)]],
    "U": [[(0, 6), (0, 1), (1, 0), (3, 0), (4, 1), (4, 6)]],
    "V": [[(0, 6), (2, 0), (4, 6)]],
    "W": [[(0, 6), (1, 0), (2, 3), (3, 0), (4, 6)]],
    "X": [[(0, 0), (4, 6)], [(0, 6), (4, 0)]],
    "Y": [[(0, 6), (2, 3), (4, 6)], [(2, 3), (2, 0)]],
    "0": [_O, [(0, 1), (4, 5)]],
    "1": [[(1, 5), (2, 6), (2, 0)], [(1, 0), (3, 0)]],
    "2": [[(0, 5), (1, 6), (3, 6), (4, 5), (4, 4), (0, 0), (4, 0)]],
    "3": [[(0, 5), (1, 6), (3, 6), (4, 5), (4, 4), (3, 3), (1, 3)], [(3, 3), (4, 2), (4, 1), (3, 0), (1, 0), (0, 1)]],
    "4": [[(3, 0), (3, 6), (0, 2), (4, 2)]],
    "5": [[(4, 6), (0, 6), (0, 3), (3, 3), (4, 2), (4, 1), (3, 0), (1, 0), (0, 1)]],
    "6": [[(4, 5), (3, 6), (1, 6), (0, 5), (0, 1), (1, 0), (3, 0), (4, 1), (4, 2), (3, 3), (0, 3)]],
    "7": [[(0, 6), (4, 6), (1, 0)]],
    "8": [[(1, 3), (0, 4), (0, 5), (1, 6), (3, 6), (4, 5), (4, 4), (3, 3), (1, 3), (0, 2), (0, 1), (1, 0),
           (3, 0), (4, 1), (4, 2), (3, 3)]],
    "9": [[(0, 1), (1, 0), (3, 0), (4, 1), (4, 5), (3, 6), (1, 6), (0, 5), (0, 4), (1, 3), (4, 3)]],
    "+": [[(0, 3), (4, 3)], [(2, 1), (2, 5)]],
    "-": [[(0, 3), (4, 3)]],
    ".": [[(2, 0), (2, 0.5)]],
    " ": [],
}


def text(s, x, y, height=1.0, vertical=False):
    k = height / 6.0
    for i, ch in enumerate(s):
        for stroke in FONT[ch]:
            pts = []
            for gx, gy in stroke:
                u, v = (gx + 6 * i) * k, gy * k
                pts.append((x - v, y + u) if vertical else (x + u, y + v))
            silk.append(pts)


def rect_silk(x0, x1, y0, y1):
    silk.append([(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)])


# ----------------------------------------------------------------------------
# Footprints
# ----------------------------------------------------------------------------
def esp_y(pin):  # pin 1..19
    return ESP_Y19 + (19 - pin) * PITCH


P = {}  # name -> (x, y) of the pads that carry a routed net
for n, name in enumerate(ESP_J2, start=1):
    P[f"ESP_{name}"] = pad(f"J1.{n}", ESP_X, esp_y(n), USED.get(name))
    for gap in ESP_ROW_GAPS:  # second row: mechanical support only, two spacings
        pad(f"J2.{n}", ESP_X + gap, esp_y(n))
for i, name in enumerate(BME_PINS):
    P[f"BME_{name}"] = pad(f"J3.{i + 1}", J_SENS_X, BME_Y0 + i * PITCH, PIN_NET.get(name))
for i, name in enumerate(SCD_PINS):
    P[f"SCD_{name}"] = pad(f"J5.{i + 1}", J_SENS_X, SCD_Y0 + i * PITCH, PIN_NET.get(name))
for i, name in enumerate(PMS_PINS):
    P[f"PMS_{name}"] = pad(f"J4.{i + 1}", PMS_X0 + i * PITCH, PMS_Y, PIN_NET[name])
C1P = pad("C1.+", 52.0, 9.0, "5V", dia=2.0)
C1N = pad("C1.-", 48.5, 9.0, "GND", dia=2.0)       # 3.5 mm lead pitch
C1N5 = pad("C1.-b", 47.0, 9.0, "GND", dia=2.0)     # 5.0 mm lead pitch
X_3V3, X_SCL, X_SDA, X_GND = 7.5, 9.0, 10.5, 12.58  # vertical buses in the sensor zone
C2A = pad("C2.1", X_3V3, 11.0, "3V3", drill=0.8)
C2B = pad("C2.2", X_GND, 11.0, "GND", drill=0.8)
npth.extend(MOUNT_HOLES)

# ----------------------------------------------------------------------------
# Routing (bottom layer everywhere, top layer only for the sensor stubs and +5V)
# ----------------------------------------------------------------------------
# PMS7003 UART + SET: nested L-shapes under the sensor, bottom layer
for sig, pin in (("PMS_TX", "TX"), ("PMS_RX", "RX"), ("PMS_SET", "SET")):
    esp = next(P[f"ESP_{k}"] for k, v in USED.items() if v == sig)
    dst = P[f"PMS_{pin}"]
    track(BOT, sig, [esp, (dst[0], esp[1]), dst], W_SIG)

# +5V: top layer along the front edge (not under the PMS7003)
track(TOP, "5V", [P["ESP_5V"], (C1P[0], ESP_Y19), C1P], W_PWR)
track(TOP, "5V", [(C1P[0], 6.6), (P["PMS_5V"][0], 6.6), P["PMS_5V"]], W_PWR)

# GND: bottom layer along the front edge, across the lower slot bridge
Y_GND = 1.5
track(BOT, "GND", [P["ESP_GND"], (61.5, P["ESP_GND"][1]), (61.5, Y_GND), (X_GND, Y_GND),
                   C2B, (X_GND, P["BME_GND"][1])], W_PWR)
track(BOT, "GND", [(C1N[0], Y_GND), C1N, C1N5], W_PWR)
track(BOT, "GND", [(P["PMS_GND"][0], Y_GND), P["PMS_GND"]], W_PWR)

# 3V3 / SCL / SDA: up the right-hand side, across the upper slot bridge, down the buses
for net, esp_key, x_up, y_band, x_bus, y_end, width in (
        ("3V3", "ESP_3V3", 62.9, 61.5, X_3V3, C2A[1], W_PWR),
        ("SCL", "ESP_IO32", 61.6, 60.0, X_SCL, P["SCD_SCL"][1], W_SIG),
        ("SDA", "ESP_IO33", 60.4, 58.6, X_SDA, P["SCD_SDA"][1], W_SIG)):
    e = P[esp_key]
    track(BOT, net, [e, (x_up, e[1]), (x_up, y_band), (x_bus, y_band), (x_bus, y_end)], width)

# Sensor sockets: top-layer stub from each pin to a via on its bus
for prefix, names in (("BME", BME_PINS), ("SCD", SCD_PINS)):
    for name in names:
        net = PIN_NET.get(name)
        if not net:
            continue  # SDO / CS stay unconnected: I2C mode, address set by the breakout (0x77)
        x_bus = {"3V3": X_3V3, "GND": X_GND, "SCL": X_SCL, "SDA": X_SDA}[net]
        p = P[f"{prefix}_{name}"]
        track(TOP, net, [p, (x_bus, p[1])], W_PWR if net in ("3V3", "GND") else W_SIG)
        via(x_bus, p[1], net)

# ----------------------------------------------------------------------------
# Silkscreen
# ----------------------------------------------------------------------------
text("AQ NODE CARRIER V1", 66.5, 57.5, 1.3)
text("ESP32-DEVKITC 38P", 68.5, 30.0, 1.0)
text("USB EDGE", 80.5, 0.8, 1.0)
rect_silk(ESP_X - 1.5, ESP_X + 26.9, 0.4, 54.6)
text("3V3", ESP_X + 1.4, esp_y(1) - 0.5)
text("5V", ESP_X + 1.4, esp_y(19) - 0.5)
text("GND", ESP_X + 1.4, esp_y(14) - 0.5)
rect_silk(*PMS_KEEPOUT[:2], PMS_KEEPOUT[2] + 1.0, H - 0.4)
text("PMS7003", 34.0, 38.0, 2.0)
text("AIR FACE THIS EDGE", 30.0, 60.0, 1.2)
text("J4 PMS7003", 26.0, 11.0, 1.0)
for i, name in enumerate(PMS_PINS):
    text(name, PMS_X0 + i * PITCH + 0.5, 7.6, 1.0, vertical=True)
text("BME680", 5.0, 54.2, 1.2)
for i, name in enumerate(BME_PINS):
    text(name, 4.6, BME_Y0 + i * PITCH - 0.5)
text("SCD41", 5.0, 26.0, 1.2)
for i, name in enumerate(SCD_PINS):
    text(name, 4.6, SCD_Y0 + i * PITCH - 0.5)
text("C1 470U", 44.0, 12.6, 1.0)
text("+", 53.4, 8.4, 1.2)
silk.append([(50.25 + 5.2 * math.cos(a), 9.0 + 5.2 * math.sin(a))
             for a in [i * math.pi / 18 for i in range(37)]])
text("C2", 14.2, 11.6, 1.0)
text("100N", 14.2, 10.0, 1.0)


# ----------------------------------------------------------------------------
# Design-rule check
# ----------------------------------------------------------------------------
def _pt_seg(px, py, s):
    dx, dy = s["x2"] - s["x1"], s["y2"] - s["y1"]
    L2 = dx * dx + dy * dy
    t = 0 if L2 == 0 else max(0, min(1, ((px - s["x1"]) * dx + (py - s["y1"]) * dy) / L2))
    return math.hypot(px - s["x1"] - t * dx, py - s["y1"] - t * dy)


def _seg_seg(a, b):
    def ccw(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    a1, a2, b1, b2 = (a["x1"], a["y1"]), (a["x2"], a["y2"]), (b["x1"], b["y1"]), (b["x2"], b["y2"])
    if ccw(a1, a2, b1) * ccw(a1, a2, b2) < 0 and ccw(b1, b2, a1) * ccw(b1, b2, a2) < 0:
        return 0.0
    return min(_pt_seg(*a1, b), _pt_seg(*a2, b), _pt_seg(*b1, a), _pt_seg(*b2, a))


def copper_items():
    items = []
    for p in pads + vias:
        for layer in (TOP, BOT):
            items.append(dict(kind="c", layer=layer, net=p["net"], x=p["x"], y=p["y"], r=p["dia"] / 2,
                              name=p.get("ref", "via")))
    for s in segs:
        items.append(dict(kind="s", r=s["w"] / 2, name=f"track {s['net']}", **s))
    return items


def gap(a, b):
    if a["kind"] == "c" and b["kind"] == "c":
        d = math.hypot(a["x"] - b["x"], a["y"] - b["y"])
    elif a["kind"] == "s" and b["kind"] == "s":
        d = _seg_seg(a, b)
    else:
        c, s = (a, b) if a["kind"] == "c" else (b, a)
        d = _pt_seg(c["x"], c["y"], s)
    return d - a["r"] - b["r"]


def drc():
    errors = []
    items = copper_items()
    # 1. clearance between different nets, per layer
    worst = 99.0
    for i, a in enumerate(items):
        for b in items[i + 1:]:
            if a["layer"] != b["layer"] or a["net"] == b["net"]:
                continue
            g = gap(a, b)
            worst = min(worst, g)
            if g < CLEARANCE - 1e-9:
                errors.append(f"clearance {g:.2f} mm: {a['name']} ({a['net']}) / {b['name']} ({b['net']}) on {a['layer']}")
    # 2. every routed net is one connected piece (plated holes join both layers)
    parent = list(range(len(items)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i, a in enumerate(items):
        for j in range(i + 1, len(items)):
            b = items[j]
            if a["net"] != b["net"]:
                continue
            same_hole = a["kind"] == b["kind"] == "c" and a["x"] == b["x"] and a["y"] == b["y"]
            if same_hole or (a["layer"] == b["layer"] and gap(a, b) < -0.05):
                parent[find(i)] = find(j)
    for net in sorted({it["net"] for it in items if not it["net"].startswith("NC")}):
        groups = {find(i) for i, it in enumerate(items) if it["net"] == net}
        if len(groups) != 1:
            errors.append(f"net {net} is split into {len(groups)} islands")
    # 3. required connections
    want = {"3V3": ["J1.1", "J3.1", "J5.1", "C2.1"], "GND": ["J1.14", "J3.2", "J5.2", "J4.2", "C1.-", "C1.-b", "C2.2"],
            "5V": ["J1.19", "J4.1", "C1.+"], "SCL": ["J1.7", "J3.3", "J5.3"], "SDA": ["J1.8", "J3.4", "J5.4"],
            "PMS_TX": ["J1.9", "J4.3"], "PMS_RX": ["J1.10", "J4.4"], "PMS_SET": ["J1.11", "J4.5"]}
    have = {}
    for p in pads:
        have.setdefault(p["net"], []).append(p["ref"])
    for net, refs in want.items():
        if sorted(have.get(net, [])) != sorted(refs):
            errors.append(f"net {net}: pads {sorted(have.get(net, []))} != expected {sorted(refs)}")
    # 4. mechanical rules
    kx0, kx1, ky0, ky1 = PMS_KEEPOUT
    sx0, sx1, sy0, sy1 = SLOT
    slot_edge = dict(kind="s", x1=(sx0 + sx1) / 2, y1=sy0 + (sx1 - sx0) / 2, x2=(sx0 + sx1) / 2,
                     y2=sy1 - (sx1 - sx0) / 2, r=(sx1 - sx0) / 2)
    for it in items:
        xs = [it["x"]] if it["kind"] == "c" else [it["x1"], it["x2"]]
        ys = [it["y"]] if it["kind"] == "c" else [it["y1"], it["y2"]]
        if min(xs) - it["r"] < EDGE_CLEARANCE or max(xs) + it["r"] > W - EDGE_CLEARANCE \
                or min(ys) - it["r"] < EDGE_CLEARANCE or max(ys) + it["r"] > H - EDGE_CLEARANCE:
            errors.append(f"{it['name']} too close to the board edge")
        if gap(it, slot_edge) < EDGE_CLEARANCE:
            errors.append(f"{it['name']} too close to the thermal slot")
        for hx, hy in npth:
            if gap(it, dict(kind="c", x=hx, y=hy, r=1.6)) < 1.0:
                errors.append(f"{it['name']} too close to a mounting hole")
        in_keepout = it["layer"] == TOP and max(xs) + it["r"] > kx0 and min(xs) - it["r"] < kx1 \
            and max(ys) + it["r"] > ky0 and min(ys) - it["r"] < ky1
        if in_keepout:
            errors.append(f"top copper under the PMS7003: {it['name']}")
    return errors, worst


# ----------------------------------------------------------------------------
# Gerber / Excellon export
# ----------------------------------------------------------------------------
def _c(v):
    return f"{int(round(v * 1e6))}"


class Gerber:
    def __init__(self, function):
        self.lines = ["%TF.GenerationSoftware,carrier.py*%", f"%TF.FileFunction,{function}*%",
                      "%FSLAX46Y46*%", "%MOMM*%", "%LPD*%"]
        self.apertures, self.body = {}, []

    def _ap(self, dia):
        key = round(dia, 4)
        if key not in self.apertures:
            self.apertures[key] = 10 + len(self.apertures)
        return self.apertures[key]

    def flash(self, x, y, dia):
        self.body.append(f"D{self._ap(dia)}*\nX{_c(x)}Y{_c(y)}D03*")

    def line(self, pts, width):
        out = [f"D{self._ap(width)}*", f"X{_c(pts[0][0])}Y{_c(pts[0][1])}D02*"]
        out += [f"X{_c(x)}Y{_c(y)}D01*" for x, y in pts[1:]]
        self.body.append("\n".join(out))

    def render(self):
        aps = [f"%ADD{n}C,{d:.4f}*%" for d, n in sorted(self.apertures.items(), key=lambda kv: kv[1])]
        return "\n".join(self.lines + aps + ["G01*"] + self.body + ["M02*"]) + "\n"


def slot_outline():
    x0, x1, y0, y1 = SLOT
    r, cx = (x1 - x0) / 2, (x0 + x1) / 2
    pts = [(x1, y0 + r), (x1, y1 - r)]
    pts += [(cx + r * math.cos(a), y1 - r + r * math.sin(a)) for a in [i * math.pi / 12 for i in range(1, 13)]]
    pts += [(x0, y0 + r)]
    pts += [(cx + r * math.cos(a), y0 + r + r * math.sin(a)) for a in [math.pi + i * math.pi / 12 for i in range(1, 13)]]
    return pts


def export(out_dir):
    out_dir.mkdir(exist_ok=True)
    files = {}
    for layer, func, name in ((TOP, "Copper,L1,Top", "F_Cu"), (BOT, "Copper,L2,Bot", "B_Cu")):
        g = Gerber(func)
        for s in segs:
            if s["layer"] == layer:
                g.line([(s["x1"], s["y1"]), (s["x2"], s["y2"])], s["w"])
        for p in pads + vias:
            g.flash(p["x"], p["y"], p["dia"])
        files[f"carrier-{name}.gbr"] = g.render()
    for func, name in (("Soldermask,Top", "F_Mask"), ("Soldermask,Bot", "B_Mask")):
        g = Gerber(func)
        for p in pads:                       # vias stay tented
            g.flash(p["x"], p["y"], p["dia"] + 0.1)
        for x, y in npth:
            g.flash(x, y, 3.6)
        files[f"carrier-{name}.gbr"] = g.render()
    g = Gerber("Legend,Top")
    for pts in silk:
        g.line(pts, W_SILK)
    files["carrier-F_Silkscreen.gbr"] = g.render()
    g = Gerber("Profile,NP")
    g.line([(0, 0), (W, 0), (W, H), (0, H), (0, 0)], 0.1)
    g.line(slot_outline(), 0.1)
    files["carrier-Edge_Cuts.gbr"] = g.render()

    def excellon(holes, plated):
        tools = sorted({d for _, _, d in holes})
        out = ["M48", f"; {'PLATED' if plated else 'NON-PLATED'} holes - carrier.py", "FMAT,2", "METRIC"]
        out += [f"T{i + 1}C{d:.3f}" for i, d in enumerate(tools)]
        out += ["%", "G90", "G05"]
        for i, d in enumerate(tools):
            out.append(f"T{i + 1}")
            out += [f"X{x:.3f}Y{y:.3f}" for x, y, dd in holes if dd == d]
        return "\n".join(out + ["M30"]) + "\n"
    files["carrier-PTH.drl"] = excellon([(p["x"], p["y"], p["drill"]) for p in pads + vias], True)
    files["carrier-NPTH.drl"] = excellon([(x, y, 3.2) for x, y in npth], False)
    for name, content in files.items():
        (out_dir / name).write_text(content)
    with zipfile.ZipFile(out_dir.parent / "carrier-gerbers.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for name, content in sorted(files.items()):
            z.writestr(name, content)
    return files


def preview(path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle, Rectangle, Polygon

    fig, ax = plt.subplots(figsize=(14, 9.8))
    ax.add_patch(Rectangle((0, 0), W, H, facecolor="#0b5d3b", edgecolor="black", lw=1.5))
    ax.add_patch(Polygon(slot_outline(), closed=True, facecolor="white", edgecolor="black"))
    scale = 72 * 14 / (W + 8) * 0.98  # points per mm, for line widths
    for layer, color, z in ((BOT, "#3d7bd9", 2), (TOP, "#d94a3d", 3)):
        for s in segs:
            if s["layer"] == layer:
                ax.plot([s["x1"], s["x2"]], [s["y1"], s["y2"]], color=color, lw=s["w"] * scale,
                        solid_capstyle="round", zorder=z, alpha=0.9)
    for p in pads + vias:
        ax.add_patch(Circle((p["x"], p["y"]), p["dia"] / 2, color="#d9b23d", zorder=4))
        ax.add_patch(Circle((p["x"], p["y"]), p["drill"] / 2, color="white", zorder=5))
    for x, y in npth:
        ax.add_patch(Circle((x, y), 1.6, facecolor="white", edgecolor="black", zorder=5))
    for pts in silk:
        ax.plot([q[0] for q in pts], [q[1] for q in pts], color="white", lw=W_SILK * scale, zorder=6)
    ax.plot([], [], color="#d94a3d", lw=3, label="top copper")
    ax.plot([], [], color="#3d7bd9", lw=3, label="bottom copper")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, 1.06), ncol=2, frameon=False)
    ax.set_xlim(-4, W + 4); ax.set_ylim(-3, H + 3); ax.set_aspect("equal"); ax.axis("off")
    fig.tight_layout()
    fig.savefig(path, dpi=120)


def main():
    here = Path(__file__).resolve().parent
    errors, worst = drc()
    print(f"{len(pads)} pads, {len(vias)} vias, {len(segs)} track segments, min clearance {worst:.2f} mm")
    for e in errors:
        print("DRC ERROR:", e)
    if errors:
        sys.exit(1)
    print("DRC OK: clearances, net connectivity, edge / slot / mounting-hole distances, PMS7003 keep-out")
    files = export(here / "gerbers")
    print("wrote", ", ".join(sorted(files)), "+ carrier-gerbers.zip")
    if "--preview" in sys.argv:
        preview(here / "preview.png")


if __name__ == "__main__":
    main()
