# AI chat on a Casio fx-9750GII

Chat with an AI model (OpenAI or Claude) from a Casio fx-9750GII graphing calculator. The
calculator is plugged into a Mac with its USB cable, and a small Python bridge on the Mac talks
to the AI service.

There are two versions:

- **AI Chat add-in** (recommended): real-time chat over USB, with answers in any language,
  Chinese included. It needs an OS that runs add-ins; it was developed on an fx-9750GII running
  the fx-9860GII OS 2.05 (hardware ID `Gy363007`).
- **BASIC mailbox chat**: for the stock fx-9750GII OS 02.02, which has no add-in support.
  ASCII only, one question per LINK transfer.

## Requirements

- macOS (answers are rendered with the system font Hiragino Sans GB)
- libusb: `brew install libusb`
- Python 3 with Pillow, plus the `anthropic` package if you use Claude
- An OpenAI or Anthropic API key

```bash
git clone https://github.com/Integ/fx-9750gii-ai-chat.git
cd fx-9750gii-ai-chat
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## AI Chat add-in (OS 2.05, real time over USB)

A gint add-in (`addin/`) talks to `aichat_usb_bridge.py` over USB with the fxlink protocol.
Nothing on the calculator depends on which AI service the bridge uses.
The Mac renders each answer into a 128-px-wide 1-bit bitmap (Hiragino Sans GB 12 px), so answers
can be in Chinese or any other language even though the keypad only types ASCII.

### Install the add-in

Download `AICHAT.g1a` from the [Releases](https://github.com/Integ/fx-9750gii-ai-chat/releases)
page (or build it, see below), then:

```bash
python3 install_addin.py AICHAT.g1a --wait   # then MENU > LINK > RECV on the calculator
```

### Start the bridge (Mac)

```bash
export OPENAI_API_KEY="your-key-here"
.venv/bin/python aichat_usb_bridge.py
```

The bridge uses OpenAI's Chat Completions API by default (`gpt-6.1-sol`, `reasoning_effort: low`,
standard library only). `--model gpt-6-luna` (cheapest) or `--model gpt-6-astra` (most capable)
picks another model, as does `OPENAI_MODEL`; `OPENAI_BASE_URL` points it at another
OpenAI-compatible server. `--provider claude` switches to Claude (`ANTHROPIC_API_KEY`).

`--echo` tests the link without the AI. `--language "..."` sets the answer language; by default the
model answers in the language you type, and pinyin gets answers in Chinese characters.
Conversation history lasts until the bridge stops (Ctrl+C) or you press F6 (NEW).

### Use it (calculator, USB cable connected)

MENU > **AI Chat**. The status line says "Mac ready" once the bridge is connected.

| Key | Typing a question | Reading an answer |
|---|---|---|
| Letter keys | letters from the red ALPHA legends (default) | |
| ALPHA or F2 | switch letters / digits and symbols | |
| F1 | upper / lower case | back to typing |
| `.` `EXP` `(-)` `^` `x²` | space `?` `,` `.` `!` in letter mode | |
| F3 / DEL / AC | space / erase / clear | |
| EXE | send | back to typing |
| UP / DOWN, LEFT / RIGHT | | scroll a line / a page |
| F4 | key help | key help |
| F5 or EXIT | show the last answer | back to typing |
| F6 | new conversation | new conversation |

### Rebuild and reinstall

```bash
source fxsdk-env.sh
(cd addin && fxsdk build-fx)
python3 install_addin.py addin/AICHAT.g1a --wait   # then LINK > RECV on the calculator
```

The fxSDK toolchain lives in `~/.local` (GiteaPC). Building it on macOS needed three local fixes,
which `giteapc install -u` may undo: `#include <string.h>` in fxsdk's `gdb-bridge.c`; dropping
`defined(TARGET_OS_MAC)` from the bundled `zlib/zutil.h` in binutils 2.42 and GCC 14.1.0; and a
stub `makeinfo` on the PATH during the build (docs are not needed). `fxsdk new` needs bash 4,
so the project was created by hand from the fxSDK templates.

## BASIC mailbox chat (stock OS 02.02 only)

The stock OS can't run add-ins or talk over USB from a BASIC program, so this version works like a
mailbox: the BASIC program stores your question in `Str 1`, the Mac picks it up while the
calculator is in LINK receive mode, asks Claude, and writes the answer back as the program `AIANS`.

```bash
python3 install_aichat.py --wait          # once: installs AICHAT and AIANS (LINK > RECV)
export ANTHROPIC_API_KEY="your-key-here"
.venv/bin/python aichat_bridge.py
```

1. MENU > PRGM > `AICHAT` > F1 (EXE). It first shows the last answer (EXE to page).
2. At `?`, type the question (SHIFT, ALPHA locks letters) and press EXE.
3. MENU > LINK > F2 (RECV). Wait for the transfer to finish.
4. Run `AICHAT` again to read the answer, and ask the next question.

Type `NEW` as the question to start a fresh conversation. The screen can only show ASCII here.

## Files

| File | Purpose |
|---|---|
| `addin/` | AI Chat add-in source (`src/main.c`), icon and CMake project; builds `AICHAT.g1a` |
| `aichat_usb_bridge.py` | Mac bridge for the add-in: fxlink over USB, OpenAI or Claude, bitmap answers |
| `openai_chat.py` | OpenAI Chat Completions client (standard library), same interface as `ClaudeChat` |
| `fxlink_usb.py` | fxlink protocol (gint add-ins) over libusb |
| `answer_render.py` | Wraps and renders answer text (CJK and Latin) to 128-px 1-bit rows |
| `install_addin.py` | Copies a `.g1a` to storage memory (Protocol 7 command 45) and verifies it |
| `fxsdk-env.sh` | `source` it to put the fxSDK toolchain on the PATH for this shell only |
| `aichat_bridge.py` | Mac bridge for the BASIC mailbox version (`Str 1` in, `AIANS` out) |
| `aichat_programs.py`, `install_aichat.py` | BASIC programs `AICHAT` / `AIANS` and their installer |
| `casio_basic.py` | Casio BASIC tokenizer and ASCII text wrapping for the 21x7 screen |
| `p7link.py` | Casio Protocol 7.00 over USB (libusb via ctypes): main memory and storage transfers |
| `p7mcs.py`, `p7_devinfo.py` | Read-only tools: device and OS info, list/dump main memory |

Main memory names found on this calculator: programs are DT `01` / `PROGRAM` / `system`;
`Str 1` is DT `04` / `STRING` / `main` / `STR1`, which the calculator only returns when asked by
name (it is absent from both the file listing and the full backup).

The 3-pin serial cable (Sunplus SPCP8x5, `04fc:0201`) works with the user-space driver in
`p7link.py` (`serial=True`) and passes a loopback test, but it outputs RS-232 levels: the signal
reaches the calculator inverted and at the wrong voltage. A 3.3 V USB-TTL adapter would be needed.

## License

MIT, see [LICENSE](LICENSE). Protocol details come from the
[Cahute](https://cahuteproject.org/) documentation; the add-in is built with
[gint and the fxSDK](https://git.planet-casio.com/Lephenixnoir/gint).
