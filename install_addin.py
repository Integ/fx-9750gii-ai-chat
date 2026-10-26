#!/usr/bin/env python3
"""Copy an add-in (.g1a) into the calculator's storage memory (fls0) over USB, then verify it.

  python3 install_addin.py addin/AICHAT.g1a [--wait]

Put the calculator in MENU > LINK > (CABL: USB) > RECV first, or pass --wait to poll for it.
Uses Protocol 7.00 command 45 "Transfer file" (storage) and 4D "Request all file information".
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from p7link import Link, P7Error, wait_for_calculator  # noqa: E402


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if len(args) != 1:
        sys.exit(__doc__)
    path = args[0]
    content = open(path, "rb").read()
    name = os.path.basename(path).upper()
    if not name.endswith(".G1A") or len(name) > 12:
        sys.exit("expected an 8.3 .g1a file name, got %r" % name)

    if "--wait" in sys.argv:
        print("Waiting for the calculator (LINK > RECV)...", flush=True)
        link = wait_for_calculator()
    else:
        link = Link()
        link.open()
        link.start()

    with link:
        print("Sending %s (%d bytes) to fls0..." % (name, len(content)), flush=True)

        def progress(i, n):
            if i == n or i % 32 == 0:
                print("  %3d%%" % (100 * i // n), flush=True)

        link.put_storage(name, content, progress=progress)
        files = link.list_storage()
        link.terminate()

    match = [f for f in files if f["name"].upper() == name]
    print("Storage memory: %s" % ", ".join("%s (%d B)" % (f["name"], f["size"]) for f in files))
    if match and match[0]["size"] == len(content):
        print("OK: %s is on the calculator (%d bytes). It appears in the main menu." % (name, len(content)))
    else:
        sys.exit("verification failed: %s not found with the expected size" % name)


if __name__ == "__main__":
    try:
        main()
    except P7Error as e:
        sys.exit("error: %s" % e)
