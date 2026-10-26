"""Tiny Casio BASIC tokenizer for fx-9860G-family calculators.

Source uses Cahute's CAT-style backslash names for opcodes outside string
literals (e.g. \\ClrText, \\->, \\Disp, \\Prog , \\Str ), one statement per line.
Everything else is copied as ASCII, which maps 1:1 onto the calculator's
character table for printable characters.

Opcode values come from Cahute's chars/chars.toml:
https://gitlab.com/cahute/cahute/-/blob/develop/chars/chars.toml
"""

import unicodedata

OPCODES = {
    "\\->": b"\x0E",
    "\\Disp": b"\x0C",
    "\\<>": b"\x11",
    "\\Prog ": b"\xED",
    "\\Getkey": b"\x7F\x8F",
    "\\If ": b"\xF7\x00",
    "\\Then ": b"\xF7\x01",
    "\\Else ": b"\xF7\x02",
    "\\IfEnd": b"\xF7\x03",
    "\\For ": b"\xF7\x04",
    "\\To ": b"\xF7\x05",
    "\\Next": b"\xF7\x07",
    "\\While ": b"\xF7\x08",
    "\\WhileEnd": b"\xF7\x09",
    "\\Do": b"\xF7\x0A",
    "\\LpWhile ": b"\xF7\x0B",
    "\\Return": b"\xF7\x0C",
    "\\Break": b"\xF7\x0D",
    "\\Stop": b"\xF7\x0E",
    "\\Locate ": b"\xF7\x10",
    "\\ClrText": b"\xF7\x18",
    "\\StrLen(": b"\xF9\x31",
    "\\StrMid(": b"\xF9\x36",
    "\\Str ": b"\xF9\x3F",
}
NEWLINE = b"\x0D"
DISP = OPCODES["\\Disp"]

# Longest names first so "\Str " does not shadow "\StrLen(".
_NAMES = sorted(OPCODES, key=len, reverse=True)


def tokenize_line(line):
    out = bytearray()
    i, in_string = 0, False
    while i < len(line):
        ch = line[i]
        if ch == '"':
            in_string = not in_string
            out.append(0x22)
            i += 1
            continue
        if not in_string and ch == "\\":
            for name in _NAMES:
                if line.startswith(name, i):
                    out += OPCODES[name]
                    i += len(name)
                    break
            else:
                raise ValueError("unknown opcode at %r" % line[i:i + 12])
            continue
        if not (0x20 <= ord(ch) < 0x7F):
            raise ValueError("non-ASCII character %r in %r" % (ch, line))
        out.append(ord(ch))
        i += 1
    return bytes(out)


def tokenize(source):
    """Join statements with newlines; a line ending in \\Disp needs no newline after it."""
    out = bytearray()
    for line in source.strip("\n").split("\n"):
        if out and not out.endswith(DISP):
            out += NEWLINE
        out += tokenize_line(line)
    return bytes(out)


def program_file(source, password=b""):
    """Main memory program file: 8-byte password, 2 zero bytes, tokens, NUL, pad to 4 bytes."""
    body = password.ljust(8, b"\x00")[:8] + b"\x00\x00" + tokenize(source) + b"\x00"
    return body + b"\x00" * (-len(body) % 4)


_REPLACEMENTS = {
    "‘": "'", "’": "'", "“": "'", "”": "'", '"': "'",
    "–": "-", "—": "-", "…": "...", "×": "*", "÷": "/",
    "≤": "<=", "≥": ">=", "≠": "!=", "→": "->", "°": " deg",
    "\\": "/", "\t": " ", " ": " ", "√": "sqrt", "π": "pi",
}


def to_calc_text(text):
    """Squash arbitrary text into printable ASCII that is safe inside a string literal."""
    out = []
    for ch in text:
        ch = _REPLACEMENTS.get(ch, ch)
        if len(ch) == 1 and not (0x20 <= ord(ch) < 0x7F) and ch != "\n":
            ch = unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode() or "?"
        out.append(ch)
    return "".join(out).replace('"', "'").replace("\\", "/")


def wrap(text, width=21):
    """Word-wrap into lines of at most `width` characters, keeping paragraph breaks."""
    lines = []
    for para in to_calc_text(text).split("\n"):
        words = para.split()
        if not words:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        cur = ""
        for w in words:
            while len(w) > width:
                if cur:
                    lines.append(cur)
                    cur = ""
                lines.append(w[:width])
                w = w[width:]
            if not cur:
                cur = w
            elif len(cur) + 1 + len(w) <= width:
                cur += " " + w
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
    while lines and lines[-1] == "":
        lines.pop()
    return lines
