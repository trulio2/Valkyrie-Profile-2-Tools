# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Compress the codec-derived TAC analysis matrices for release packaging."""

from __future__ import annotations

import argparse
import zlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
OUTPUT = ROOT / "opensource" / "data" / "tac"
MATRICES = {
    "analysis-pair.f32.zlib": "tac_analysis_pair_pinv_0.01.bin",
    "overlap.f32.zlib": "tac_overlap_matrix.bin",
}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source", nargs="?", type=Path, default=ROOT / ".cache",
        help="folder containing the generated uncompressed matrices",
    )
    args = parser.parse_args(argv)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for target_name, source_name in MATRICES.items():
        source = args.source / source_name
        raw = source.read_bytes()
        target = OUTPUT / target_name
        target.write_bytes(zlib.compress(raw, 9))
        print("%s: %d -> %d bytes" % (target, len(raw), target.stat().st_size))


if __name__ == "__main__":
    main()

