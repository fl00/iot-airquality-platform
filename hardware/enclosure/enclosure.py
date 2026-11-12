#!/usr/bin/env python3
"""
Parametric enclosure for the air-quality node built on the carrier PCB
(hardware/pcb): ESP32-DevKitC + BME680 + SCD41 plugged into the board,
PMS7003 lying on it.

Every feature is an axis-aligned box that is added to or cut from the part, so
the script needs nothing but numpy: it evaluates the boxes on a rectilinear
grid and writes watertight binary STLs, plus an OpenSCAD export of the very
same boxes for anyone who prefers to tweak the model there.

    python3 enclosure.py            # -> body.stl, lid.stl, enclosure.scad
    python3 enclosure.py --preview  # also renders preview.png (needs matplotlib)

Units: millimetres. X = length, Y = depth, Z = up. The PCB origin sits at
(PCB_X0, PCB_Y0); its USB edge faces the Y = 0 wall, the PMS7003 air face the
opposite wall. Layout along X:  [ BME680 + SCD41 chamber | PMS7003 | ESP32 ]
"""
import struct
import sys
from pathlib import Path

import numpy as np

# ----------------------------------------------------------------------------
# Parameters (PCB figures must match hardware/pcb/carrier.py)
# ----------------------------------------------------------------------------
WALL = 2.0
LID_T = 2.0
FIT = 0.25             # lid lip clearance per side
PCB_W, PCB_H, PCB_T = 94.0, 64.0, 1.6
PCB_GAP = 1.0          # clearance between board edge and walls
STANDOFF_H = 3.0       # room for the solder joints under the board
INNER_H = 28.0         # cavity height (Dupont housings on J4 are the tallest item)
MOUNT_HOLES = [(3.5, 3.5), (3.5, 60.5), (76.5, 60.5), (77.4, 3.5)]   # PCB coordinates
PEG = 2.2              # square peg entering the 3.2 mm mounting holes
SLOT_X = (21.0, 22.6)  # PCB thermal slot, sealed by the lid partition
PMS_X = (23.0, 60.6)   # PMS7003 footprint on the PCB (37 mm + clearance)
PMS_L, PMS_H = 48.4, 12.0
ESP_CENTRE_X = 64.7 + 12.7
SOCKET_H = 8.5         # 2.54 mm female header height
USB_W = 14.0

# ----------------------------------------------------------------------------
# Derived layout
# ----------------------------------------------------------------------------
PCB_X0 = PCB_Y0 = WALL + PCB_GAP
X_IN1 = WALL + PCB_W + 2 * PCB_GAP
Y_IN1 = WALL + PCB_H + 2 * PCB_GAP
OUT_X, OUT_Y = X_IN1 + WALL, Y_IN1 + WALL
Z0 = WALL
Z_PCB_TOP = Z0 + STANDOFF_H + PCB_T
Z_TOP = WALL + INNER_H
X_PART = (PCB_X0 + SLOT_X[0] - 0.2, PCB_X0 + SLOT_X[1] + 0.2)   # lid partition
X_PMS0, X_PMS1 = PCB_X0 + PMS_X[0], PCB_X0 + PMS_X[1]
Y_PMS0 = Y_IN1 - PMS_L                                           # connector end of the PMS7003
Z_PMS_TOP = Z_PCB_TOP + PMS_H


def box(x0, x1, y0, y1, z0, z1):
    assert x1 > x0 and y1 > y0 and z1 > z0, (x0, x1, y0, y1, z0, z1)
    return (x0, x1, y0, y1, z0, z1)


def body_ops():
    """Ordered list of (+1 | -1, box)."""
    ops = [(+1, box(0, OUT_X, 0, OUT_Y, 0, Z_TOP)),
           (-1, box(WALL, X_IN1, WALL, Y_IN1, Z0, Z_TOP + 1))]

    # --- PCB standoffs with locating pegs ------------------------------------
    for hx, hy in MOUNT_HOLES:
        x, y = PCB_X0 + hx, PCB_Y0 + hy
        ops.append((+1, box(x - 3, x + 3, y - 3, y + 3, Z0, Z0 + STANDOFF_H)))
        ops.append((+1, box(x - PEG / 2, x + PEG / 2, y - PEG / 2, y + PEG / 2, Z0, Z_PCB_TOP + 2.0)))
    # Pad under the PMS7003 (track-free area) so the lid can clamp the sensor
    ops.append((+1, box(PCB_X0 + 39, PCB_X0 + 45, PCB_Y0 + 44, PCB_Y0 + 50, Z0, Z0 + STANDOFF_H)))
    # Rib closing the gap under the board, below the thermal slot
    ops.append((+1, box(X_PART[0], X_PART[1], WALL, Y_IN1, Z0, Z0 + STANDOFF_H - 0.3)))

    # ------------------------------ cuts ------------------------------------
    z_v0, z_v1 = Z_PCB_TOP + 2.4, Z_TOP - 8
    # Sensor chamber vents: end wall + both side walls
    y = WALL + 6
    while y + 2.5 <= Y_IN1 - 4:
        ops.append((-1, box(-1, WALL + 0.5, y, y + 2.5, z_v0, z_v1)))
        y += 5.0
    x = WALL + 3
    while x + 2.5 <= X_PART[0] - 1:
        ops.append((-1, box(x, x + 2.5, -1, WALL + 0.5, z_v0, z_v1)))
        ops.append((-1, box(x, x + 2.5, Y_IN1 - 0.5, OUT_Y + 1, z_v0, z_v1)))
        x += 5.0
    # PMS7003 grille (Y = max wall): 3 mm slots / 1.2 mm bars over the 37 x 12 mm face
    x = X_PMS0 + 2.0
    while x + 3.0 <= X_PMS1 - 2.0:
        ops.append((-1, box(x, x + 3.0, Y_IN1 - 0.5, OUT_Y + 1, Z_PCB_TOP + 1.5, Z_PMS_TOP - 1.5)))
        x += 4.2
    # USB opening (Y = 0 wall); the connector sits on top of the ESP32 board
    xu = PCB_X0 + ESP_CENTRE_X - 0.6        # splits the difference between both row spacings
    z_usb = Z_PCB_TOP + SOCKET_H + 1.6
    ops.append((-1, box(xu - USB_W / 2, xu + USB_W / 2, -1, WALL + 0.5, z_usb - 3.2, z_usb + 6.3)))
    return ops


def lid_ops():
    """Lid in assembled position (plate on top of the body, features hanging down)."""
    zt = Z_TOP
    ops = [(+1, box(0, OUT_X, 0, OUT_Y, zt, zt + LID_T)),
           # locating lip
           (+1, box(WALL + FIT, X_IN1 - FIT, WALL + FIT, Y_IN1 - FIT, zt - 4, zt)),
           (-1, box(WALL + FIT + 1.6, X_IN1 - FIT - 1.6, WALL + FIT + 1.6, Y_IN1 - FIT - 1.6, zt - 5, zt)),
           # partition closing the sensor chamber, down to just above the PCB slot
           (+1, box(X_PART[0], X_PART[1], WALL + FIT, Y_IN1 - FIT, Z_PCB_TOP + 0.3, zt)),
           # PMS7003: hold-down post, side fence, two stops at its connector end
           (+1, box(X_PMS0 + 14, X_PMS1 - 8, Y_PMS0 + 22, Y_PMS0 + 26, Z_PMS_TOP + 0.4, zt)),
           (+1, box(X_PMS1, X_PMS1 + 1.6, Y_PMS0 - 1.6, Y_IN1 - 6, Z_PCB_TOP + 3.4, zt)),
           (+1, box(X_PART[1], X_PMS0 + 4, Y_PMS0 - 1.6, Y_PMS0, Z_PCB_TOP + 3.4, zt)),
           (+1, box(X_PMS1 - 4, X_PMS1 + 1.6, Y_PMS0 - 1.6, Y_PMS0, Z_PCB_TOP + 3.4, zt))]
    # Hot-air exhaust above the ESP32
    y = WALL + 10
    while y + 2.5 <= Y_IN1 - 8:
        ops.append((-1, box(PCB_X0 + ESP_CENTRE_X - 10.5, PCB_X0 + ESP_CENTRE_X + 10.5, y, y + 2.5,
                            zt - 0.5, zt + LID_T + 1)))
        y += 6.0
    return ops


def lid_print_orientation(ops):
    """Rotate 180 deg about X so the plate lies on the bed."""
    top = Z_TOP + LID_T
    return [(s, box(x0, x1, OUT_Y - y1, OUT_Y - y0, top - z1, top - z0))
            for s, (x0, x1, y0, y1, z0, z1) in ops]


# ----------------------------------------------------------------------------
# Rectilinear CSG -> watertight mesh
# ----------------------------------------------------------------------------
def voxelise(ops):
    axes = []
    for a in range(3):
        vals = sorted({round(b[2 * a + k], 3) for _, b in ops for k in (0, 1)})
        axes.append(np.array(vals))
    solid = np.zeros([len(ax) - 1 for ax in axes], dtype=bool)
    for sign, b in ops:
        sl = tuple(slice(int(np.searchsorted(axes[a], round(b[2 * a], 3))),
                         int(np.searchsorted(axes[a], round(b[2 * a + 1], 3)))) for a in range(3))
        solid[sl] = sign > 0
    return axes, solid


def mesh(ops):
    """Returns (quads [n,4,3], normals [n,3]); one quad per exposed cell face."""
    axes, solid = voxelise(ops)
    padded = np.pad(solid, 1)
    quads, normals = [], []
    for a in range(3):
        b, c = (a + 1) % 3, (a + 2) % 3
        for direction in (-1, +1):
            neighbour = np.roll(padded, -direction, axis=a)[1:-1, 1:-1, 1:-1]
            for idx in np.argwhere(solid & ~neighbour):
                pa = axes[a][idx[a] + (direction > 0)]
                b0, b1 = axes[b][idx[b]], axes[b][idx[b] + 1]
                c0, c1 = axes[c][idx[c]], axes[c][idx[c] + 1]
                corners = [(b0, c0), (b1, c0), (b1, c1), (b0, c1)]
                if direction < 0:
                    corners.reverse()
                quad = np.zeros((4, 3))
                quad[:, a] = pa
                quad[:, b] = [p[0] for p in corners]
                quad[:, c] = [p[1] for p in corners]
                n = np.zeros(3)
                n[a] = direction
                quads.append(quad)
                normals.append(n)
    return np.array(quads), np.array(normals), axes, solid


def check_manifold(quads):
    """Every directed edge must be matched by exactly one opposite edge."""
    from collections import Counter
    edges = Counter()
    for q in np.round(quads, 3):
        pts = [tuple(p) for p in q]
        for i in range(4):
            edges[(pts[i], pts[(i + 1) % 4])] += 1
    bad = sum(1 for (p, q), n in edges.items() if n != 1 or edges.get((q, p), 0) != 1)
    return bad


def write_stl(path, quads, normals):
    with open(path, "wb") as f:
        f.write(b"air-quality node enclosure".ljust(80, b" "))
        f.write(struct.pack("<I", 2 * len(quads)))
        for q, n in zip(quads, normals):
            for tri in ((q[0], q[1], q[2]), (q[0], q[2], q[3])):
                f.write(struct.pack("<3f", *n))
                for p in tri:
                    f.write(struct.pack("<3f", *p))
                f.write(b"\0\0")


def scad_module(name, ops):
    """Sequential add/cut list -> nested OpenSCAD difference()/union()."""
    expr = ""
    for sign, (x0, x1, y0, y1, z0, z1) in ops:
        cube = f"translate([{x0:g},{y0:g},{z0:g}]) cube([{x1 - x0:g},{y1 - y0:g},{z1 - z0:g}]);"
        if not expr:
            expr = cube
        elif sign > 0:
            expr = f"union() {{\n{expr}\n{cube}\n}}"
        else:
            expr = f"difference() {{\n{expr}\n{cube}\n}}"
    return f"module {name}() {{\n{expr}\n}}\n"


def preview(parts, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    fig = plt.figure(figsize=(14, 7))
    light = np.array([0.35, -0.5, 0.8])
    light /= np.linalg.norm(light)
    for i, (title, quads, normals) in enumerate(parts):
        ax = fig.add_subplot(1, len(parts), i + 1, projection="3d")
        shade = 0.45 + 0.5 * np.clip(normals @ light, 0, 1)
        colors = np.outer(shade, [0.55, 0.75, 0.95])
        ax.add_collection3d(Poly3DCollection(quads, facecolors=colors, edgecolors="none"))
        ax.set_xlim(0, OUT_X); ax.set_ylim(-16, OUT_X - 16); ax.set_zlim(0, OUT_X)
        ax.set_box_aspect((1, 1, 1)); ax.view_init(elev=42, azim=-62)
        ax.set_axis_off(); ax.set_title(title)
    fig.tight_layout()
    fig.savefig(path, dpi=110)


def main():
    out = Path(__file__).resolve().parent
    body = body_ops()
    lid_assembled = lid_ops()
    lid = lid_print_orientation(lid_assembled)
    parts = []
    for name, ops in (("body", body), ("lid", lid)):
        quads, normals, axes, solid = mesh(ops)
        bad = check_manifold(quads)
        vol = float(np.einsum("i,j,k,ijk->", np.diff(axes[0]), np.diff(axes[1]), np.diff(axes[2]), solid))
        write_stl(out / f"{name}.stl", quads, normals)
        print(f"{name}.stl: {2 * len(quads)} triangles, {vol / 1000:.1f} cm3, non-manifold edges: {bad}")
        parts.append((name, quads, normals))
    (out / "enclosure.scad").write_text(
        "// Generated by enclosure.py - edit the parameters there, or tweak the boxes here.\n"
        f"// Outer size: {OUT_X:g} x {OUT_Y:g} x {Z_TOP + LID_T:g} mm\n\n"
        + scad_module("body", body) + "\n" + scad_module("lid", lid)
        + f"\nbody();\ntranslate([0, {OUT_Y + 10:g}, 0]) lid();\n")
    print(f"outer size: {OUT_X:g} x {OUT_Y:g} x {Z_TOP + LID_T:g} mm")
    if "--preview" in sys.argv:
        preview(parts, out / "preview.png")


if __name__ == "__main__":
    main()
