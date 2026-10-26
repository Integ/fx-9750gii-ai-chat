#!/usr/bin/env python3
"""Mac side of the AICHAT add-in: real-time AI chat with the calculator over USB.

The add-in (gint, fxlink protocol) sends application "aichat" messages:
  question   UTF-8 text typed on the keypad -> status "thinking", then answer
  reset      start a new conversation       -> status
The Mac answers with:
  status     short ASCII text for the add-in's status line ("Mac ready" on connection,
             which is what lets the add-in send questions)
  answer     the reply rendered as a 128-px-wide 1-bit bitmap (any language, incl. Chinese)

  .venv/bin/python aichat_usb_bridge.py                    chat with OpenAI (needs OPENAI_API_KEY)
  .venv/bin/python aichat_usb_bridge.py --model gpt-6-luna  pick another OpenAI model
  .venv/bin/python aichat_usb_bridge.py --provider claude  chat with Claude (needs ANTHROPIC_API_KEY)
  .venv/bin/python aichat_usb_bridge.py --echo             no AI, echo the question (tests the USB link)
"""

import argparse
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from aichat_bridge import MODEL, ClaudeChat, EchoResponder  # noqa: E402
from answer_render import answer_payload  # noqa: E402
from fxlink_usb import FxlinkDevice, FxlinkError  # noqa: E402
from openai_chat import DEFAULT_MODEL as OPENAI_DEFAULT_MODEL, OpenAIChat  # noqa: E402

APP = "aichat"

SYSTEM_PROMPT = """You are chatting with someone through a Casio graphing calculator.
Your reply is shown on a 128x64 monochrome screen: about 20 Latin letters or 10 Chinese characters per line,
4 lines visible at a time, scrollable.

- Plain text only: no markdown, LaTeX, tables, bullet symbols or emoji.
- Be concise: usually under 300 characters. Go longer only when asked for detail.
- The user types on a calculator keypad (ASCII only), so messages may be all caps, lack punctuation,
  contain typos, or be written in pinyin. Read them charitably.
- Reply in {language}.
- Write math inline in calculator style, e.g. x^2+3x-4=0 gives x=1 or x=-4; use sqrt(), pi, ^, *, /."""

DEFAULT_LANGUAGE = ("the language the user writes in; if they write in pinyin (Chinese in Latin letters), "
                    "reply in Simplified Chinese characters")


def clean(text):
    """Strip leftover markdown that would look odd on the calculator."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"^#+\s*", "", text, flags=re.M)
    return text.replace("`", "")


def serve(dev, responder, log):
    """Handle messages from one connected calculator until it goes away."""
    # The add-in only sends questions once it has heard from us.
    dev.send(APP, "status", "Mac ready")
    while True:
        msg = dev.recv(500)
        if msg is None:
            continue
        if msg.application == "fxlink" and msg.type == "text":
            log("calc: %s" % msg.data.decode("utf-8", "replace").rstrip())
            continue
        if msg.application != APP:
            continue
        if msg.type == "hello":
            log("Add-in started")
            dev.send(APP, "status", "Mac ready")
        elif msg.type == "reset":
            responder.reset()
            log("New conversation")
            dev.send(APP, "status", "New conversation")
        elif msg.type == "question":
            question = msg.data.decode("utf-8", "replace").strip()
            if not question:
                continue
            log("Q: %s" % question)
            dev.send(APP, "status", "Thinking...")
            started = time.monotonic()
            answer = clean(responder.ask(question))
            log("A (%.1fs): %s\n" % (time.monotonic() - started, answer))
            dev.send(APP, "answer", answer_payload(answer, question=question))


def main():
    parser = argparse.ArgumentParser(description="USB bridge for the AICHAT add-in")
    parser.add_argument("--echo", action="store_true", help="test mode: no AI, echo the question back")
    parser.add_argument("--provider", choices=("openai", "claude"), default="openai",
                        help="AI service (default: openai, key in OPENAI_API_KEY)")
    parser.add_argument("--model", help="OpenAI model (default: $OPENAI_MODEL or %s)" % OPENAI_DEFAULT_MODEL)
    parser.add_argument("--language", default=DEFAULT_LANGUAGE, help="language for the answers")
    parser.add_argument("-v", "--verbose", action="store_true", help="log fxlink messages")
    args = parser.parse_args()

    def log(text):
        print(text, flush=True)

    if args.echo:
        responder, label = EchoResponder(), "echo test mode"
    elif args.provider == "openai":
        if not os.environ.get("OPENAI_API_KEY"):
            log("Warning: OPENAI_API_KEY is not set; answers will be an error message until it is.\n")
        responder = OpenAIChat(args.language, SYSTEM_PROMPT, model=args.model)
        label = "OpenAI %s" % responder.model
    else:
        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            log("Warning: ANTHROPIC_API_KEY is not set; answers will be an error message until it is.\n")
        responder, label = ClaudeChat(args.language, SYSTEM_PROMPT), "Claude %s" % MODEL
    log("AICHAT USB bridge (%s). Start the AICHAT add-in on the calculator.\n" % label)

    waiting_logged = False
    while True:
        dev = FxlinkDevice(verbose=args.verbose)
        try:
            dev.open()
        except FxlinkError:
            if not waiting_logged:
                log("Waiting for the add-in...")
                waiting_logged = True
            time.sleep(1)
            continue
        waiting_logged = False
        log("Calculator connected.")
        try:
            with dev:
                serve(dev, responder, log)
        except FxlinkError as e:
            log("Connection ended: %s\n" % e)
        time.sleep(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nBridge stopped.")
