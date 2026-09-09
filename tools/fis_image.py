#!/usr/bin/env python3
r"""py -3 tools/fis_image.py scan <iso> --out <dir>
    py -3 tools/fis_image.py png <item.fis> <out.png>
    py -3 tools/fis_image.py encode <item.fis> <in.png> <out.fis>
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from scripts.fis_images import (                                # noqa: F401
    CSM1_4, FisError, MAGIC, PACK_DIRECTORY, PSMT4, PSMT8,
    apply_pack, descriptor, encode, indices, items_in, pack_name, palette,
    payloads, png, read_png, render, views,
)
from scripts import triace_ps2_unpack as triace
from scripts.vp2_dcms import read_entry


def cmd_scan(args):
    found = 0
    with open(args.iso, "rb") as handle:
        _name, total, table = triace.load_table(handle)
        for resource in range(args.first, min(args.last + 1, total)):
            try:
                raw = bytes(read_entry(handle, table, total, resource))
            except Exception:                                    # noqa: BLE001
                continue
            if not raw:
                continue
            seen = set()
            for where, blob in payloads(raw, resource):
                for at, item in items_in(blob):
                    key = hash(item)
                    if key in seen:
                        continue
                    seen.add(key)
                    try:
                        meta = descriptor(item)
                    except FisError:
                        continue
                    found += 1
                    print("#%-5d %-12s 0x%-7X %4dx%-4d %dbpp draw %4dx%-4d "
                          "%7d B  tag=%s"
                          % (resource, where, at, meta["width"],
                             meta["height"], meta["bits"], meta["draw_width"],
                             meta["draw_height"], meta["size"],
                             item[0x14:0x18].decode("latin-1")))
                    if args.out:
                        safe = "".join(c if c.isalnum() else "-"
                                       for c in where).strip("-")
                        name = os.path.join(
                            args.out, "fis-%04d-%s-%X.png"
                            % (resource, safe, at))
                        try:
                            render(item, name)
                        except Exception as error:               # noqa: BLE001
                            print("      render failed: %s" % error)
            if args.progress and resource % 128 == 0:
                print("  ... entry %d, %d item(s)" % (resource, found),
                      file=sys.stderr, flush=True)
    print("%d FIS item(s)" % found)


def cmd_report(args):
    item = open(args.item, "rb").read()
    meta = descriptor(item)
    for key in sorted(meta):
        print("  %-18s %s" % (key, meta[key]))
    alphas = [colour[3] for colour in palette(item, meta)]
    print("  %-18s %s" % ("palette alphas", alphas[:16]))


def cmd_png(args):
    item = open(args.item, "rb").read()
    meta = render(item, args.out)
    print("%s: %dx%d %dbpp -> %s"
          % (args.item, meta["width"], meta["height"], meta["bits"], args.out))


def cmd_encode(args):
    with open(args.item, "rb") as handle:
        item = handle.read()
    built, approximated = encode(item, args.png)
    with open(args.out, "wb") as handle:
        handle.write(built)
    same = "identical" if built == item else "changed"
    print("%s + %s -> %s  (%d bytes, %s, %d pixel(s) matched approximately)"
          % (args.item, args.png, args.out, len(built), same, approximated))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    scan = commands.add_parser("scan", help="inventory FIS items on an ISO")
    scan.add_argument("iso")
    scan.add_argument("--first", type=int, default=0)
    scan.add_argument("--last", type=int, default=1 << 30)
    scan.add_argument("--out", help="also render every item into this directory")
    scan.add_argument("--progress", action="store_true")
    scan.set_defaults(func=cmd_scan)

    report = commands.add_parser("report", help="describe one extracted item")
    report.add_argument("item")
    report.set_defaults(func=cmd_report)

    to_png = commands.add_parser("png", help="render one item to PNG")
    to_png.add_argument("item")
    to_png.add_argument("out")
    to_png.set_defaults(func=cmd_png)

    from_png = commands.add_parser(
        "encode", help="put a PNG back into an item, same length")
    from_png.add_argument("item")
    from_png.add_argument("png")
    from_png.add_argument("out")
    from_png.set_defaults(func=cmd_encode)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
