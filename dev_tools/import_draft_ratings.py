"""Read-only XLSX import, preserving original cells and contradictory ratings."""
import hashlib
import json
from pathlib import Path
import sys
from xml.etree import ElementTree as ET
from zipfile import ZipFile

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def import_ratings(path):
    path = Path(path)
    with ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            shared = ["".join(s.itertext()) for s in
                      ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", NS)]

        def rows(sheet):
            result = []
            for row in ET.fromstring(z.read("xl/worksheets/sheet%d.xml" % sheet)).findall(".//m:row", NS):
                cells = {}
                for cell in row:
                    v = cell.find("m:v", NS)
                    value = v.text if v is not None else ""
                    if cell.get("t") == "s":
                        value = shared[int(value)]
                    elif cell.get("t") == "inlineStr":
                        value = "".join(cell.find("m:is", NS).itertext())
                    cells[cell.get("r")] = value
                result.append((int(row.get("r")), cells))
            return result

        profiles = {}
        for row, cells in rows(1):
            if row == 1:
                continue
            name = cells.get("C%d" % row, "").strip()
            if not name:
                continue
            def number(col):
                try:
                    return float(cells.get("%s%d" % (col, row), ""))
                except ValueError:
                    return None
            strength, combo, composite = number("E"), number("G"), number("H")
            flags = []
            if strength is None:
                flags.append("missing_strength")
            if None not in (strength, combo, composite) and abs(.6*strength+.4*combo-composite) > .11:
                flags.append("composite_formula_mismatch")
            if name == "帝释天":
                flags.append("partner_and_avoid_conflict_requires_review")
            if name in profiles:
                raise ValueError("Duplicate name: " + name)
            profiles[name] = dict(strength=strength, role=cells.get("D%d" % row, ""),
                                  source="评分总表!E%d" % row, flags=flags, cells=cells)
        return dict(schema_version=1, source_file=path.name,
                    sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    profiles=profiles, combinations=[cells for row, cells in rows(2) if row > 1])


if __name__ == "__main__":
    output = Path(sys.argv[2])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(import_ratings(sys.argv[1]), ensure_ascii=False, indent=2), encoding="utf-8")
