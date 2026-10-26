"""Render chat text into a 128-pixel-wide 1-bit bitmap for the calculator screen.

Rows are 16 bytes, most significant bit = leftmost pixel, 1 = black: the same
layout as gint's VRAM on the fx-9860G, so the add-in can copy rows straight in.
CJK text breaks between characters, Latin text between words, and closing
punctuation is kept off the start of a line.
"""

import struct
import unicodedata

from PIL import Image, ImageDraw, ImageFont

WIDTH = 128
FONT_PATH = "/System/Library/Fonts/Hiragino Sans GB.ttc"
FONT_SIZE = 12
LINE_HEIGHT = 13
MAX_HEIGHT = 1600  # 25 KB on the calculator (~120 lines); longer answers are cut with a note
NO_LINE_START = set("，。、！？；：）」』】》,.!?;:)]}%")

_font = ImageFont.truetype(FONT_PATH, FONT_SIZE)
_measure = ImageDraw.Draw(Image.new("1", (1, 1)))


def _width(text):
    return _measure.textlength(text, font=_font)


def _is_cjk(ch):
    return unicodedata.east_asian_width(ch) in ("W", "F")


def _tokens(paragraph):
    """Split into breakable units: single CJK characters, Latin words, and spaces."""
    out, word = [], ""
    for ch in paragraph:
        if ch == " " or _is_cjk(ch):
            if word:
                out.append(word)
                word = ""
            out.append(ch)
        else:
            word += ch
    if word:
        out.append(word)
    return out


def _split_long(token):
    """A Latin word wider than the screen is cut into pieces that fit."""
    pieces, cur = [], ""
    for ch in token:
        if cur and _width(cur + ch) > WIDTH:
            pieces.append(cur)
            cur = ch
        else:
            cur += ch
    return pieces + [cur] if cur else pieces


def wrap(text):
    lines = []
    for paragraph in text.replace("\r", "").replace("\t", " ").split("\n"):
        line = ""
        for token in _tokens(paragraph):
            for piece in (_split_long(token) if _width(token) > WIDTH else [token]):
                if _width(line + piece) <= WIDTH:
                    line += piece
                    continue
                if piece == " ":
                    lines.append(line)
                    line = ""
                elif piece in NO_LINE_START and line:
                    # pull the last character down with the punctuation
                    lines.append(line[:-1])
                    line = line[-1] + piece
                else:
                    lines.append(line.rstrip())
                    line = piece
        lines.append(line.rstrip())
    while lines and not lines[-1]:
        lines.pop()
    return lines


def render(text, question=None):
    """Bitmap for an answer, optionally headed by the question in an inverted bar."""
    lines = wrap(text)
    q_lines = wrap(question) if question else []
    bar = len(q_lines) * LINE_HEIGHT + 2 if q_lines else 0  # inverted question bar, 1 px padding
    gap = 3 if q_lines else 0
    height = bar + gap + len(lines) * LINE_HEIGHT
    if height > MAX_HEIGHT:
        keep = (MAX_HEIGHT - bar - gap) // LINE_HEIGHT - 1
        lines = lines[:max(keep, 0)] + ["(...回答太长，已截断)"]
        height = bar + gap + len(lines) * LINE_HEIGHT
    img = Image.new("1", (WIDTH, max(height, 1)), 1)
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"
    y = 0
    if q_lines:
        draw.rectangle((0, 0, WIDTH - 1, bar - 1), fill=0)
        for i, line in enumerate(q_lines):
            draw.text((1, 1 + i * LINE_HEIGHT), line, font=_font, fill=1)
        y = bar + gap
    for line in lines:
        draw.text((0, y), line, font=_font, fill=0)
        y += LINE_HEIGHT
    return img


def to_rows(img):
    """Payload for the calculator: u16 height (big-endian), then height * 16 bytes."""
    inverted = img.point(lambda p: 0 if p else 1, mode="1")  # PIL: 1 = white; calculator: 1 = black
    return struct.pack(">H", img.height) + inverted.tobytes()


def answer_payload(text, question=None):
    return to_rows(render(text, question))
