#!/usr/bin/env python3
"""Export JLCPCB assembly files (gerbers, BOM, CPL) into ./jlcpcb/.

BOM rows come from the PCB footprints (so off-board parts like the CN2 mating
plug are skipped automatically) and are grouped by LCSC part number.
"""
import csv
import re
import shutil
import subprocess
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

KICAD_CLI = shutil.which("kicad-cli") or "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"
ROOT = Path(__file__).resolve().parent
PCB = ROOT / "fahrenheat.kicad_pcb"
OUT = ROOT / "jlcpcb"


def natural(ref):
    m = re.match(r"([A-Za-z]+)(\d+)", ref)
    return (m.group(1), int(m.group(2))) if m else (ref, 0)


def run(*args):
    subprocess.run([KICAD_CLI, *args], check=True, capture_output=True, text=True)


def footprints():
    """Yield (ref, value, footprint, lcsc, x, y, rot, side) via kicad-cli pos export."""
    tmp = OUT / "_pos.csv"
    run("pcb", "export", "pos", str(PCB), "--format", "csv", "--units", "mm",
        "--side", "both", "--exclude-dnp", "-o", str(tmp))
    rows = list(csv.DictReader(tmp.open()))
    tmp.unlink()
    return rows


def lcsc_fields():
    """Map reference -> LCSC property, read straight from the PCB file."""
    text = PCB.read_text()
    out = {}
    for m in re.finditer(r'\n\t\(footprint "[^"]+"(.*?)\n\t\)', text, re.S):
        props = dict(re.findall(r'\(property "([^"]+)" "([^"]*)"', m.group(1)))
        if "exclude_from_bom" in m.group(1):
            continue
        out[props.get("Reference")] = props.get("LCSC", "")
    return out


def main():
    OUT.mkdir(exist_ok=True)
    lcsc = lcsc_fields()
    pos = [r for r in footprints() if r["Ref"] in lcsc]

    missing = [r["Ref"] for r in pos if not lcsc[r["Ref"]]]
    if missing:
        sys.exit(f"Missing LCSC property on: {', '.join(missing)}")

    groups = defaultdict(list)
    for r in pos:
        groups[(r["Val"], r["Package"].split(":")[-1], lcsc[r["Ref"]])].append(r["Ref"])
    with (OUT / "fahrenheat-bom.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Comment", "Designator", "Footprint", "LCSC Part #"])
        for (val, fp, part), refs in sorted(groups.items(), key=lambda g: natural(min(g[1], key=natural))):
            w.writerow([val, ",".join(sorted(refs, key=natural)), fp, part])

    with (OUT / "fahrenheat-cpl.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
        for r in sorted(pos, key=lambda r: natural(r["Ref"])):
            layer = "Top" if r["Side"] == "top" else "Bottom"
            w.writerow([r["Ref"], f'{float(r["PosX"]):.4f}mm', f'{float(r["PosY"]):.4f}mm',
                        layer, f'{float(r["Rot"]):.0f}'])

    gerber_dir = OUT / "_gerbers"
    shutil.rmtree(gerber_dir, ignore_errors=True)
    gerber_dir.mkdir()
    run("pcb", "export", "gerbers", str(PCB), "-o", str(gerber_dir) + "/",
        "--layers", "F.Cu,B.Cu,F.Paste,B.Paste,F.Silkscreen,B.Silkscreen,F.Mask,B.Mask,Edge.Cuts",
        "--no-protel-ext", "--subtract-soldermask")
    run("pcb", "export", "drill", str(PCB), "-o", str(gerber_dir) + "/",
        "--format", "excellon", "--excellon-separate-th", "--generate-map", "--map-format", "gerberx2")
    with zipfile.ZipFile(OUT / "fahrenheat-gerbers.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(gerber_dir.iterdir()):
            z.write(p, p.name)
    shutil.rmtree(gerber_dir)

    print(f"{len(pos)} placements, {len(groups)} BOM lines -> {OUT}")


if __name__ == "__main__":
    main()
