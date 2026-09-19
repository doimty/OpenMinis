#!/usr/bin/env python3
"""Replay chat geometry evidence without echoing private log text.

Exit 1: an observed render rejection or outer markdown measurement exceeds
its recorded viewport. Exit 0: bounded live measurements with no such error.
Exit 2: no comparable live measurement; absence of a warning is not a pass.
TextContainerGuard size probes alone are NOT rendered-frame evidence.
This diagnoses a captured run; it does not run UIKit or prove a source fix.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re

NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
VIEWPORT = re.compile(r"\[cv-bounds\].*?\bnew=(" + NUMBER + r")x(" + NUMBER + r")")
MEASURE = re.compile(r"\bmeasureW=(" + NUMBER + r")\s+tcW=(" + NUMBER + r")\s+lastH=(" + NUMBER + r")")
LAYER = re.compile(r"Ignoring bogus layer size\s*\((" + NUMBER + r"),\s*(" + NUMBER + r")\)")


def analyze(text):
    viewport_width = None
    viewport_widths = set()
    bounded_samples = 0
    measurements = []
    rejected_layers = []
    for line_number, line in enumerate(text.splitlines(), 1):
        viewport = VIEWPORT.search(line)
        if viewport:
            width = float(viewport[1])
            if math.isfinite(width) and width > 1:
                viewport_width = width
                viewport_widths.add(width)
        rejected = LAYER.search(line)
        if rejected:
            rejected_layers.append({"line": line_number, "width": float(rejected[1]),
                                    "height": float(rejected[2])})
        # The live text-view correction path, not a sizeThatFits probe or an
        # unrelated UIKit text container used by a horizontal code/table view.
        if "[CellSize]" not in line or "[invalidateCell]" not in line:
            continue
        measured = MEASURE.search(line)
        if not measured or viewport_width is None:
            continue
        width, container_width, height = map(float, measured.groups())
        sample = {"line": line_number, "viewport_width": viewport_width,
                  "measure_width": width, "container_width": container_width,
                  "height": height}
        measurements.append(sample)
        if 1 < width <= viewport_width + 1:
            bounded_samples += 1
    escaped = [s for s in measurements if s["measure_width"] > s["viewport_width"] + 1]
    status = "fail" if rejected_layers or escaped else "pass" if bounded_samples else "inconclusive"
    return {"status": status, "viewport_widths": sorted(viewport_widths),
            "bounded_live_samples": bounded_samples,
            "out_of_viewport_live_samples": escaped,
            "rejected_layer_count": len(rejected_layers),
            "rejected_layer_examples": rejected_layers[:5]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("--report", type=Path, help="write numeric-only JSON evidence")
    args = parser.parse_args()
    data = args.log.read_bytes()
    result = analyze(data.decode("utf-8", errors="replace"))
    result["source_sha256"] = hashlib.sha256(data).hexdigest()
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered)
    print(rendered, end="")
    return {"pass": 0, "fail": 1, "inconclusive": 2}[result["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
