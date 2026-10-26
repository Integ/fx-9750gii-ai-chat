#!/usr/bin/env python3
"""Install the AICHAT and AIANS programs onto the calculator, then read them back to verify.

Put the calculator in MENU > LINK > (CABL: USB) > RECV, or pass --wait to poll for it.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from aichat_programs import ANSWER_NAME, MAIN_NAME, WELCOME_TEXT, answer_program, main_program  # noqa: E402
from p7link import Link, P7Error, wait_for_calculator  # noqa: E402

PROGRAM = {"dt": 0x01, "dir": "system", "group": "PROGRAM"}


def main():
    files = {MAIN_NAME: main_program(), ANSWER_NAME: answer_program(WELCOME_TEXT)}
    if "--wait" in sys.argv:
        print("Waiting for the calculator (LINK > RECV)...", flush=True)
        link = wait_for_calculator()
    else:
        link = Link()
        link.open()
        link.start()

    with link:
        for name, content in files.items():
            print("Sending %-8s (%d bytes)" % (name, len(content)))
            link.put_mcs(PROGRAM["dt"], PROGRAM["dir"], name, PROGRAM["group"], content)

        listed = [f for f in link.list_mcs() if f["group"].startswith("PROGRAM")]
        print("Programs on calculator:", ", ".join("%s (%d B, dir=%r, dt=%02X)" % (
            f["name"], f["size"], f["dir"], f["dt"]) for f in listed) or "none")

        ok = True
        for name, content in files.items():
            _, back = link.get_mcs(PROGRAM["dt"], PROGRAM["dir"], name, PROGRAM["group"])
            same = back == content
            ok &= same
            print("Read back %-8s %d bytes: %s" % (name, len(back), "identical" if same else "DIFFERENT"))
            if not same:
                print("   sent: %s" % content.hex(" "))
                print("   got:  %s" % back.hex(" "))
        link.terminate()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    try:
        main()
    except P7Error as e:
        sys.exit("error: %s" % e)
