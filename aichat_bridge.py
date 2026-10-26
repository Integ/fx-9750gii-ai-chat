#!/usr/bin/env python3
"""Mac side of AI chat for a Casio fx-9750GII (stock OS, Casio BASIC + LINK mode).

Each time the calculator enters LINK > RECV, the bridge:
  1. reads Str 1 (the question typed in the AICHAT program),
  2. asks Claude (keeping the conversation history while the bridge runs),
  3. writes the answer back as program AIANS and ends the session.
Then run AICHAT on the calculator to read the answer and ask the next question.

  python3 aichat_bridge.py            chat with Claude (needs the anthropic SDK + credentials)
  python3 aichat_bridge.py --echo     no AI: answer with the question itself, to test the link
  options: --language "English"       language for the answers (ASCII only on the calculator)
           -v                         log Protocol 7.00 packets
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from aichat_programs import ANSWER_NAME, answer_program  # noqa: E402
from p7link import P7Error, wait_for_calculator  # noqa: E402

MODEL = "claude-opus-5-5"
PROGRAM = {"dt": 0x01, "dir": "system", "group": "PROGRAM"}
# Strings never show up in the 2D listing or the 29 backup; they must be requested by name.
STR1 = {"dt": 0x04, "dir": "main", "name": "STR1", "group": "STRING"}
RESET_WORDS = {"NEW", "RESET", "CLEAR"}
MULTIBYTE_LEADS = {0x7F, 0xE5, 0xE6, 0xE7, 0xF7, 0xF9}

SYSTEM_PROMPT = """You are answering questions typed on a Casio fx-9750GII graphing calculator.
Your reply is shown on a monochrome text screen 21 characters wide, 7 lines per page.

- Write plain {language} using printable ASCII only: no markdown, LaTeX, emoji, tables or bullet symbols.
- Be brief: usually under 300 characters (two screens). Go longer only when the user asks for detail.
- The question was typed on a calculator keypad, so it may be all caps, lack punctuation or contain typos. Read it charitably.
- Write math inline with calculator-style notation, e.g. x^2+3x-4=0 gives x=1 or x=-4; use sqrt(), pi, ^, *, /."""


def read_str1(link):
    """Str 1 contents, or None if the calculator has no Str 1 (it answers the request with a NAK)."""
    try:
        _, raw = link.get_mcs(STR1["dt"], STR1["dir"], STR1["name"], STR1["group"])
    except P7Error as e:
        if "refused" in str(e):
            return None
        raise
    return raw


def decode_calc_string(raw):
    """Calculator string bytes (NUL-padded) -> text. Printable ASCII is kept, opcodes become '?'."""
    out, i = [], 0
    raw = raw.split(b"\x00", 1)[0]
    while i < len(raw):
        b = raw[i]
        if b in MULTIBYTE_LEADS and i + 1 < len(raw):
            out.append("?")
            i += 2
            continue
        out.append(chr(b) if 0x20 <= b < 0x7F else (" " if b == 0x0D else "?"))
        i += 1
    return "".join(out).strip()


def api_error_message(error):
    """The human-readable message from an API error response body, if there is one."""
    body = error.body if isinstance(error.body, dict) else {}
    detail = body.get("error", {}).get("message") if isinstance(body.get("error"), dict) else None
    return detail or str(error)


class ClaudeChat:
    def __init__(self, language, system_template=SYSTEM_PROMPT):
        import anthropic  # imported lazily so --echo works without the SDK

        self.anthropic = anthropic
        self.client = anthropic.Anthropic()
        self.system = system_template.format(language=language)
        self.history = []

    def reset(self):
        self.history = []

    def ask(self, question):
        anthropic = self.anthropic
        messages = self.history + [{"role": "user", "content": question}]
        try:
            response = self.client.beta.messages.create(
                model=MODEL,
                max_tokens=16000,
                system=self.system,
                messages=messages,
                output_config={"effort": "low"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except (anthropic.AuthenticationError, TypeError):
            # TypeError: the SDK found no credentials at all.
            return "Mac error: the Claude API key is missing or invalid. Set ANTHROPIC_API_KEY and restart the bridge."
        except anthropic.PermissionDeniedError:
            return "Mac error: this API key is not allowed to use %s." % MODEL
        except anthropic.RateLimitError:
            return "Mac error: rate limited by the API. Wait a minute and try again."
        except anthropic.APIStatusError as e:
            detail = api_error_message(e)
            print("API error %d: %s" % (e.status_code, detail), flush=True)
            if "credit balance" in detail.lower():
                return ("Mac error: the API account has no credits left. Add credits in the Anthropic "
                        "Console (Plans & Billing), then ask again.")
            return "Mac error: API returned %d: %s" % (e.status_code, detail[:160])
        except anthropic.APIConnectionError:
            return "Mac error: could not reach the API. Check the internet connection."

        if response.stop_reason == "refusal":
            return "Sorry, I can't help with that question."
        answer = "".join(b.text for b in response.content if b.type == "text").strip()
        if not answer:
            return "(empty answer, please ask again)"
        self.history = messages + [{"role": "assistant", "content": answer}]
        return answer


def serve_once(link, responder, state, verbose):
    raw = read_str1(link)
    if raw is None:
        reply = "No question found. Run AICHAT, type a question, then come back to LINK > RECV."
        question = ""
    else:
        if verbose:
            print("  Str 1 raw: %s" % raw.hex(" "))
        question = decode_calc_string(raw)
        if not question:
            reply = "No new question.\n\nLast answer:\n" + (state.get("last") or "(none yet)")
        elif question.upper() in RESET_WORDS:
            responder.reset()
            reply = "New conversation started. Ask away!"
        else:
            print("Q: %s" % question, flush=True)
            started = time.monotonic()
            reply = responder.ask(question)
            print("A (%.1fs): %s\n" % (time.monotonic() - started, reply), flush=True)
            state["last"] = reply

    link.send(0x05, 0x01)  # regular check: is the calculator still listening after the API call?
    link.expect_ack("regular check")
    link.put_mcs(PROGRAM["dt"], PROGRAM["dir"], ANSWER_NAME, PROGRAM["group"], answer_program(reply))
    link.terminate()
    return question


class EchoResponder:
    def reset(self):
        pass

    def ask(self, question):
        return "Echo test OK. The Mac received:\n\n%s\n\n(%d characters)" % (question, len(question))


def main():
    parser = argparse.ArgumentParser(description="Casio fx-9750GII <-> Claude chat bridge")
    parser.add_argument("--echo", action="store_true", help="test mode: no AI, echo the question back")
    parser.add_argument("--language", default="English", help="answer language (must be writable in ASCII)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    if not args.echo and not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        print("Warning: ANTHROPIC_API_KEY is not set; answers will be an error message until it is.\n", flush=True)
    responder = EchoResponder() if args.echo else ClaudeChat(args.language)
    state = {}
    print("AI chat bridge ready (%s). On the calculator: run AICHAT, then LINK > RECV.\n"
          % ("echo test mode" if args.echo else MODEL), flush=True)
    while True:
        link = wait_for_calculator()
        link.verbose = args.verbose
        try:
            with link:
                serve_once(link, responder, state, args.verbose)
                print("Answer sent. Run AICHAT on the calculator to read it.\n", flush=True)
        except P7Error as e:
            print("Link error: %s (will retry on the next RECV)\n" % e, flush=True)
        time.sleep(2)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nBridge stopped.")
