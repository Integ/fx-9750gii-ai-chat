#!/usr/bin/env python3
"""Inspect a Casio fx-9860G-family calculator's main memory over USB.

  python3 p7mcs.py info              device information (hardware ID, OS version)
  python3 p7mcs.py list              list main memory files
  python3 p7mcs.py dump [DIR]        list, then download every file into DIR (default: mcs_dump)

Add --wait to keep polling until the calculator enters RECV mode, -v to log packets,
--serial to go through the 3-pin port with the SPCP8x5 USB-serial cable instead of USB.

Read-only. Put the calculator in MENU > LINK > (CABL: USB or 3PIN) > RECV first.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from p7link import Link, P7Error, wait_for_calculator  # noqa: E402


def hexdump(data, width=16):
    for off in range(0, len(data), width):
        chunk = data[off:off + width]
        hx = " ".join("%02X" % b for b in chunk)
        asc = "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in chunk)
        print("    %04X  %-*s  %s" % (off, width * 3, hx, asc))


def print_files(files):
    print("%-4s %-16s %-10s %-10s %8s" % ("DT", "GROUP", "DIR", "NAME", "SIZE"))
    for f in files:
        print("%02X   %-16s %-10s %-10s %8d" % (f["dt"], f["group"], f["dir"], f["name"].encode("latin-1").hex() if not f["name"].isascii() else f["name"], f["size"]))


STRING_GUESSES = [
    (dt, group, "main", name)
    for dt in (0x04, 0x00, 0x05, 0x85)
    for group in ("STRING", "STRING 1")
    for name in ("STR1", "STR 1", "1")
]


def probe_strings():
    """Find how Str 1 is named: full backup first (command 29), then direct requests with guessed names."""
    print("Waiting for the calculator (LINK > RECV)...", flush=True)
    link = wait_for_calculator(verbose="-v" in sys.argv)
    with link:
        print("\n== Command 29: every main memory file ==")
        all_files = link.get_all_mcs()
        for f in all_files:
            if f["group"].startswith("ALPHA"):
                continue
            print("%02X %-12s %-8s %-8s %5d  %s" % (f["dt"], f["group"], f["dir"], f["name"].encode("latin-1").hex()
                  if not f["name"].isascii() else f["name"], f["size"], f["content"][:40].hex(" ")))
        strings = [f for f in all_files if "STR" in f["group"].upper() or "STR" in f["name"].upper()]
        if strings:
            print("\nString entries found in the backup:")
            for f in strings:
                print("  %r" % {k: v for k, v in f.items() if k != "content"})
                hexdump(f["content"])
        else:
            print("\n== No string in the backup; trying direct requests ==")
            for dt, group, directory, name in STRING_GUESSES:
                try:
                    header, content = link.get_mcs(dt, directory, name, group)
                except P7Error as e:
                    print("  DT=%02X %-9s %-6s -> %s" % (dt, group, name, e))
                    if "timed out" in str(e):
                        print("  link no longer answering, stopping probe")
                        return
                    continue
                print("  DT=%02X %-9s %-6s -> FOUND %r, %d bytes" % (dt, group, name, header, len(content)))
                hexdump(content)
                break
        link.terminate()


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if args and args[0] == "probe-strings":
        return probe_strings()
    if not args or args[0] not in ("info", "list", "dump"):
        sys.exit(__doc__)
    cmd = args[0]
    out_dir = args[1] if len(args) > 1 else "mcs_dump"

    verbose = "-v" in sys.argv
    serial = "--serial" in sys.argv
    if "--wait" in sys.argv:
        print("Waiting for the calculator (LINK > RECV%s)..." % (", 3PIN cable" if serial else ""), flush=True)
        link = wait_for_calculator(verbose=verbose, serial=serial)
    else:
        link = Link(verbose=verbose, serial=serial)
        link.open()
        link.start()
    with link:
        if cmd == "info":
            print(link.device_info())
            link.terminate()
            return
        files = link.list_mcs()
        print_files(files)
        if cmd == "dump":
            os.makedirs(out_dir, exist_ok=True)
            for f in files:
                try:
                    header, content = link.get_mcs(f["dt"], f["dir"], f["name"], f["group"])
                except P7Error as e:
                    print("\n[%s/%s] failed: %s" % (f["group"], f["name"], e))
                    continue
                safe = "".join(c if c.isascii() and c.isalnum() else "_" for c in "%s_%s_%s" % (f["group"], f["dir"], f["name"]))
                with open(os.path.join(out_dir, safe + ".bin"), "wb") as fp:
                    fp.write(content)
                print("\n[%s / %s / %s] DT=%02X header=%r, %d bytes" % (
                    f["group"], f["dir"], f["name"], header["dt"], header["fields"], len(content)))
                hexdump(content[:256])
        link.terminate()


if __name__ == "__main__":
    try:
        main()
    except P7Error as e:
        sys.exit("error: %s" % e)
