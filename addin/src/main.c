/* AI Chat: talk to an AI model from the calculator through a USB bridge.

   The add-in exchanges fxlink messages (application "aichat") with
   aichat_usb_bridge.py on the computer:
     calculator -> host:  question (ASCII text), reset
     host -> calculator:  status (short ASCII text),
                          answer (u16 height, then height rows of 16 bytes:
                          a 128-pixel-wide 1-bit bitmap, 1 = black)
   The host renders answers to bitmaps, so they can be in any language. */

#include <gint/display.h>
#include <gint/keyboard.h>
#include <gint/timer.h>
#include <gint/usb.h>
#include <gint/usb-ff-bulk.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#define APP          "aichat"
#define MAX_INPUT    240
#define MAX_ROWS     2048
#define VIEW_ROWS    56           /* answer area above the bottom bar */
#define ROW_BYTES    (DWIDTH / 8)
#define SCROLL_STEP  13           /* one rendered line of text */
#define TEXT_COLS    21           /* 128 px at 6 px per character */
#define USB_TIMEOUT  3000         /* ms */

enum mode { MODE_INPUT, MODE_WAIT, MODE_VIEW, MODE_HELP };

static enum mode mode = MODE_INPUT, mode_before_help = MODE_INPUT;
static char input[MAX_INPUT + 1];
static int input_len;
static bool letters = true;       /* letter keys, or digits and symbols */
static bool lower = false;

static uint8_t *answer;           /* answer_rows * ROW_BYTES */
static int answer_rows;
static int scroll;

static char status[40] = "Connect the USB cable";
static bool bridge_ready;         /* the host has sent us at least one message */
static int ticks;                 /* 100 ms ticks */

static usb_interface_t const *interfaces[] = { &usb_ff_bulk, NULL };

static void set_status(char const *text)
{
	strncpy(status, text, sizeof status - 1);
	status[sizeof status - 1] = 0;
}

//---
// USB
//---

static bool send_msg(char const *type, void const *data, int size)
{
	if(!usb_is_open())
		return false;
	usb_fxlink_header_t header;
	if(!usb_fxlink_fill_header(&header, APP, type, size))
		return false;

	int pipe = usb_ff_bulk_output();
	timeout_t tm = timeout_make_ms(USB_TIMEOUT);
	if(usb_write_sync_timeout(pipe, &header, sizeof header, false, &tm) < 0)
		return false;
	if(size > 0 && usb_write_sync_timeout(pipe, data, size, false, &tm) < 0)
		return false;
	return usb_commit_sync_timeout(pipe, &tm) >= 0;
}

/* Read exactly [size] bytes of the current message; reads stop at transaction
   boundaries and often return less than requested, hence the loop. */
static bool read_exact(void *buffer, int size)
{
	int done = 0;
	while(done < size) {
		timeout_t tm = timeout_make_ms(USB_TIMEOUT);
		int rc = usb_read_sync_timeout(usb_ff_bulk_input(),
			(uint8_t *)buffer + done, size - done, false, &tm);
		if(rc <= 0)
			return false;
		done += rc;
	}
	return true;
}

static void receive_status(int size)
{
	char text[sizeof status];
	int keep = size < (int)sizeof text - 1 ? size : (int)sizeof text - 1;
	if(!read_exact(text, keep)) {
		usb_fxlink_drop_transaction();
		return;
	}
	text[keep] = 0;
	if(keep < size)
		usb_fxlink_drop_transaction();
	set_status(text);
}

static void receive_answer(int size)
{
	uint8_t head[2];
	if(size < 2 || !read_exact(head, 2)) {
		usb_fxlink_drop_transaction();
		return;
	}
	int rows = (head[0] << 8) | head[1];
	if(rows <= 0 || rows > MAX_ROWS || size != 2 + rows * ROW_BYTES) {
		usb_fxlink_drop_transaction();
		set_status("Bad answer format");
		mode = MODE_INPUT;
		return;
	}

	free(answer);
	answer = malloc(rows * ROW_BYTES);
	answer_rows = 0;
	if(!answer) {
		usb_fxlink_drop_transaction();
		set_status("Answer too big for RAM");
		mode = MODE_INPUT;
		return;
	}
	if(!read_exact(answer, rows * ROW_BYTES)) {
		free(answer);
		answer = NULL;
		set_status("Transfer failed");
		mode = MODE_INPUT;
		return;
	}

	answer_rows = rows;
	scroll = 0;
	input_len = 0;
	input[0] = 0;
	set_status("");
	mode = MODE_VIEW;
}

static void poll_usb(void)
{
	if(!usb_is_open()) {
		if(bridge_ready || status[0] == 0)
			set_status("Connect the USB cable");
		bridge_ready = false;
		return;
	}
	if(!bridge_ready && strcmp(status, "Connect the USB cable") == 0)
		set_status("Start the bridge on Mac");

	usb_fxlink_header_t header;
	while(usb_fxlink_handle_messages(&header)) {
		bridge_ready = true;
		if(!strncmp(header.application, APP, 16)
				&& !strncmp(header.type, "status", 16))
			receive_status(header.size);
		else if(!strncmp(header.application, APP, 16)
				&& !strncmp(header.type, "answer", 16))
			receive_answer(header.size);
		else
			usb_fxlink_drop_transaction();
	}
}

static void submit(void)
{
	if(input_len == 0)
		return;
	if(!bridge_ready) {
		set_status("Start the bridge on Mac");
		return;
	}
	if(!send_msg("question", input, input_len)) {
		set_status("USB send failed");
		return;
	}
	set_status("Sent");
	mode = MODE_WAIT;
}

static void new_chat(void)
{
	if(bridge_ready)
		send_msg("reset", NULL, 0);
	free(answer);
	answer = NULL;
	answer_rows = 0;
	scroll = 0;
	mode = MODE_INPUT;
	set_status("New conversation");
}

//---
// Keyboard input
//---

struct keymap { uint8_t key; char const *text; };

/* Letter mode follows the red ALPHA legends on the keyboard */
static struct keymap const letter_keys[] = {
	{ KEY_XOT, "A" }, { KEY_LOG, "B" }, { KEY_LN, "C" }, { KEY_SIN, "D" },
	{ KEY_COS, "E" }, { KEY_TAN, "F" }, { KEY_FRAC, "G" }, { KEY_FD, "H" },
	{ KEY_LEFTP, "I" }, { KEY_RIGHTP, "J" }, { KEY_COMMA, "K" },
	{ KEY_ARROW, "L" }, { KEY_7, "M" }, { KEY_8, "N" }, { KEY_9, "O" },
	{ KEY_4, "P" }, { KEY_5, "Q" }, { KEY_6, "R" }, { KEY_MUL, "S" },
	{ KEY_DIV, "T" }, { KEY_1, "U" }, { KEY_2, "V" }, { KEY_3, "W" },
	{ KEY_ADD, "X" }, { KEY_SUB, "Y" }, { KEY_0, "Z" },
	{ KEY_DOT, " " }, { KEY_EXP, "?" }, { KEY_NEG, "," }, { KEY_POWER, "." },
	{ KEY_SQUARE, "!" },
	{ 0, NULL },
};

static struct keymap const number_keys[] = {
	{ KEY_0, "0" }, { KEY_1, "1" }, { KEY_2, "2" }, { KEY_3, "3" },
	{ KEY_4, "4" }, { KEY_5, "5" }, { KEY_6, "6" }, { KEY_7, "7" },
	{ KEY_8, "8" }, { KEY_9, "9" }, { KEY_DOT, "." }, { KEY_EXP, "=" },
	{ KEY_NEG, "-" }, { KEY_ADD, "+" }, { KEY_SUB, "-" }, { KEY_MUL, "*" },
	{ KEY_DIV, "/" }, { KEY_LEFTP, "(" }, { KEY_RIGHTP, ")" },
	{ KEY_COMMA, "," }, { KEY_POWER, "^" }, { KEY_SQUARE, "^2" },
	{ KEY_XOT, "x" }, { KEY_FRAC, "/" }, { KEY_ARROW, "?" },
	{ KEY_LOG, "log(" }, { KEY_LN, "ln(" }, { KEY_SIN, "sin(" },
	{ KEY_COS, "cos(" }, { KEY_TAN, "tan(" },
	{ 0, NULL },
};

static void type_text(char const *text)
{
	for(; *text && input_len < MAX_INPUT; text++) {
		char c = *text;
		if(letters && lower && c >= 'A' && c <= 'Z')
			c += 'a' - 'A';
		input[input_len++] = c;
	}
	input[input_len] = 0;
}

static void clamp_scroll(void)
{
	int max = answer_rows - VIEW_ROWS;
	if(scroll > max) scroll = max;
	if(scroll < 0) scroll = 0;
}

static void key_input(int key, bool repeat)
{
	if(key == KEY_DEL) {
		if(input_len > 0)
			input[--input_len] = 0;
		return;
	}
	if(repeat)
		return;

	switch(key) {
	case KEY_EXE:   submit(); return;
	case KEY_ACON:  input_len = 0; input[0] = 0; return;
	case KEY_F1:    lower = !lower; letters = true; return;
	case KEY_F2:
	case KEY_ALPHA: letters = !letters; return;
	case KEY_F3:    type_text(" "); return;
	case KEY_F5:
	case KEY_EXIT:  if(answer) mode = MODE_VIEW; return;
	case KEY_F6:    new_chat(); return;
	}

	for(struct keymap const *k = letters ? letter_keys : number_keys; k->text; k++) {
		if(k->key == key) {
			type_text(k->text);
			return;
		}
	}
}

static void key_view(int key, bool repeat)
{
	switch(key) {
	case KEY_UP:    scroll -= SCROLL_STEP; break;
	case KEY_DOWN:  scroll += SCROLL_STEP; break;
	case KEY_LEFT:  scroll -= VIEW_ROWS - SCROLL_STEP; break;
	case KEY_RIGHT: scroll += VIEW_ROWS - SCROLL_STEP; break;
	case KEY_EXE:
	case KEY_EXIT:
	case KEY_F1:    if(!repeat) mode = MODE_INPUT; break;
	case KEY_F6:    if(!repeat) new_chat(); break;
	}
	clamp_scroll();
}

static void handle_key(key_event_t ev)
{
	bool repeat = (ev.type == KEYEV_HOLD);

	if(mode == MODE_HELP) {
		if(!repeat) mode = mode_before_help;
		return;
	}
	if(ev.key == KEY_F4 && !repeat && mode != MODE_WAIT) {
		mode_before_help = mode;
		mode = MODE_HELP;
		return;
	}
	if(mode == MODE_WAIT) {
		if(ev.key == KEY_EXIT && !repeat) {
			mode = MODE_INPUT;
			set_status("Stopped waiting");
		}
		return;
	}
	if(mode == MODE_VIEW)
		key_view(ev.key, repeat);
	else
		key_input(ev.key, repeat);
}

//---
// Drawing
//---

static void draw_title(char const *right)
{
	drect(0, 0, DWIDTH - 1, 8, C_BLACK);
	dtext(2, 1, C_WHITE, "AI Chat");
	if(right)
		dtext_opt(DWIDTH - 2, 1, C_WHITE, C_NONE, DTEXT_RIGHT, DTEXT_TOP,
			right, -1);
}

static void draw_fkeys(char const *labels[6])
{
	for(int i = 0; i < 6; i++) {
		if(!labels[i] || !labels[i][0])
			continue;
		int x = i * 21 + 1;
		drect(x, 57, x + 19, DHEIGHT - 1, C_BLACK);
		dtext_opt(x + 10, 57, C_WHITE, C_NONE, DTEXT_CENTER, DTEXT_TOP,
			labels[i], -1);
	}
}

/* Draw [text] wrapped at TEXT_COLS, keeping the last [max_lines] lines */
static void draw_wrapped(char const *text, int len, int y, int max_lines)
{
	int lines = (len + TEXT_COLS - 1) / TEXT_COLS;
	int first = lines > max_lines ? lines - max_lines : 0;
	for(int l = first; l < lines; l++) {
		int n = len - l * TEXT_COLS;
		if(n > TEXT_COLS) n = TEXT_COLS;
		dtext_opt(1, y + (l - first) * 8, C_BLACK, C_NONE, DTEXT_LEFT,
			DTEXT_TOP, text + l * TEXT_COLS, n);
	}
}

static void draw_input(void)
{
	draw_title(letters ? (lower ? "abc" : "ABC") : "123");

	/* Typed text with a blinking cursor */
	char shown[MAX_INPUT + 2];
	memcpy(shown, input, input_len);
	shown[input_len] = (ticks % 10 < 5) ? '_' : ' ';
	shown[input_len + 1] = 0;
	if(input_len == 0 && ticks % 10 >= 5)
		dtext(1, 11, C_BLACK, "Ask a question...");
	else
		draw_wrapped(shown, input_len + 1, 11, 4);

	dhline(46, C_BLACK);
	dtext(1, 48, C_BLACK, status);

	char const *labels[6] = { lower ? "A" : "a", letters ? "123" : "ABC",
		"SPC", "HELP", answer ? "ANS" : "", "NEW" };
	draw_fkeys(labels);
}

static void draw_wait(void)
{
	draw_title(NULL);
	draw_wrapped(input, input_len, 11, 3);

	static char const *dots[] = { "Thinking", "Thinking.", "Thinking..",
		"Thinking..." };
	dtext_opt(DWIDTH / 2, 38, C_BLACK, C_NONE, DTEXT_CENTER, DTEXT_TOP,
		dots[(ticks / 3) % 4], -1);
	dhline(46, C_BLACK);
	dtext(1, 48, C_BLACK, status);

	char const *labels[6] = { "", "", "", "", "", "" };
	draw_fkeys(labels);
	dtext_opt(DWIDTH - 2, 57, C_BLACK, C_NONE, DTEXT_RIGHT, DTEXT_TOP,
		"EXIT:stop", -1);
}

static void draw_view(void)
{
	/* The answer rows are in the VRAM's own format: copy them straight in */
	uint8_t *vram = (uint8_t *)gint_vram;
	for(int r = 0; r < VIEW_ROWS && scroll + r < answer_rows; r++)
		memcpy(vram + r * ROW_BYTES, answer + (scroll + r) * ROW_BYTES,
			ROW_BYTES);

	char const *labels[6] = { "ASK", "", "", "HELP", "", "NEW" };
	draw_fkeys(labels);

	/* Scroll position, e.g. "3/9" in screens of text */
	if(answer_rows > VIEW_ROWS) {
		int total = (answer_rows + VIEW_ROWS - 1) / VIEW_ROWS;
		int page = (scroll + VIEW_ROWS - 1) / VIEW_ROWS + 1;
		if(scroll + VIEW_ROWS >= answer_rows) page = total;
		char pos[16];
		int n = 0;
		if(page >= 10) pos[n++] = '0' + page / 10;
		pos[n++] = '0' + page % 10;
		pos[n++] = '/';
		if(total >= 10) pos[n++] = '0' + total / 10;
		pos[n++] = '0' + total % 10;
		pos[n] = 0;
		dtext_opt(64, 57, C_BLACK, C_NONE, DTEXT_CENTER, DTEXT_TOP, pos, -1);
	}
}

static void draw_help(void)
{
	static char const *lines[] = {
		"Letters: ALPHA keys",
		" .=space EXP=? (-)=,",
		" ^=.  x2=!  F1:a/A",
		"123: F2 or ALPHA key",
		" EXP:= (-):- x2:^2",
		"DEL:erase AC:clear",
		"EXE:send F6:new chat",
		"UP/DOWN: scroll",
	};
	for(int i = 0; i < 8; i++)
		dtext(1, i * 8, C_BLACK, lines[i]);
}

static void draw(void)
{
	dclear(C_WHITE);
	switch(mode) {
	case MODE_INPUT: draw_input(); break;
	case MODE_WAIT:  draw_wait(); break;
	case MODE_VIEW:  draw_view(); break;
	case MODE_HELP:  draw_help(); break;
	}
	dupdate();
}

//---
// Main loop
//---

int main(void)
{
	usb_open(interfaces, GINT_CALL_NULL);

	/* A 100 ms tick interrupts getkey_opt() so USB keeps being serviced */
	static volatile int tick;
	int timer = timer_configure(TIMER_ANY, 100000, GINT_CALL_SET(&tick));
	if(timer >= 0)
		timer_start(timer);

	int const options = GETKEY_MENU | GETKEY_MENU_DUPDATE | GETKEY_MENU_EVENT
		| GETKEY_REP_ARROWS | GETKEY_REP_ALL;

	while(1) {
		draw();
		tick = 0;
		key_event_t ev = getkey_opt(options, &tick);

		if(ev.type == KEYEV_NONE) {
			ticks++;
			poll_usb();
		}
		else if(ev.type == KEYEV_OSMENU) {
			/* Back from the main menu: make sure the link is still open */
			if(!usb_is_open())
				usb_open(interfaces, GINT_CALL_NULL);
		}
		else {
			handle_key(ev);
		}
	}

	if(timer >= 0)
		timer_stop(timer);
	usb_close();
	return 1;
}
