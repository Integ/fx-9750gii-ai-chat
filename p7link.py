"""Minimal Casio Protocol 7.00 link over USB, for fx-9860G-family calculators.

Uses libusb-1.0 through ctypes, so it has no Python dependencies.
The calculator must be in MENU > LINK, cable = USB, RECV (passive side).

Protocol reference (Cahute project):
  https://cahuteproject.org/topics/protocols/seven/packet-format.html
  https://cahuteproject.org/topics/protocols/seven/flows.html
  https://cahuteproject.org/topics/protocols/seven/casio-commands.html
"""

import ctypes
import ctypes.util
import time

CASIO_VID = 0x07CF
SEVEN_PIDS = (0x6101, 0x6102)
LIBUSB_ERROR_TIMEOUT = -7

T_COMMAND = 0x01
T_DATA = 0x02
T_ROLESWAP = 0x03
T_CHECK = 0x05
T_ACK = 0x06
T_NAK = 0x15
T_TERMINATE = 0x18

LIBUSB_CANDIDATES = (
    "/opt/homebrew/lib/libusb-1.0.dylib",
    "/usr/local/lib/libusb-1.0.dylib",
    ctypes.util.find_library("usb-1.0"),
)


class P7Error(Exception):
    pass


class EndpointDesc(ctypes.Structure):
    _fields_ = [
        ("bLength", ctypes.c_uint8),
        ("bDescriptorType", ctypes.c_uint8),
        ("bEndpointAddress", ctypes.c_uint8),
        ("bmAttributes", ctypes.c_uint8),
        ("wMaxPacketSize", ctypes.c_uint16),
        ("bInterval", ctypes.c_uint8),
        ("bRefresh", ctypes.c_uint8),
        ("bSynchAddress", ctypes.c_uint8),
        ("extra", ctypes.c_void_p),
        ("extra_length", ctypes.c_int),
    ]


class InterfaceDesc(ctypes.Structure):
    _fields_ = [
        ("bLength", ctypes.c_uint8),
        ("bDescriptorType", ctypes.c_uint8),
        ("bInterfaceNumber", ctypes.c_uint8),
        ("bAlternateSetting", ctypes.c_uint8),
        ("bNumEndpoints", ctypes.c_uint8),
        ("bInterfaceClass", ctypes.c_uint8),
        ("bInterfaceSubClass", ctypes.c_uint8),
        ("bInterfaceProtocol", ctypes.c_uint8),
        ("iInterface", ctypes.c_uint8),
        ("endpoint", ctypes.POINTER(EndpointDesc)),
        ("extra", ctypes.c_void_p),
        ("extra_length", ctypes.c_int),
    ]


class Interface(ctypes.Structure):
    _fields_ = [
        ("altsetting", ctypes.POINTER(InterfaceDesc)),
        ("num_altsetting", ctypes.c_int),
    ]


class ConfigDesc(ctypes.Structure):
    _fields_ = [
        ("bLength", ctypes.c_uint8),
        ("bDescriptorType", ctypes.c_uint8),
        ("wTotalLength", ctypes.c_uint16),
        ("bNumInterfaces", ctypes.c_uint8),
        ("bConfigurationValue", ctypes.c_uint8),
        ("iConfiguration", ctypes.c_uint8),
        ("bmAttributes", ctypes.c_uint8),
        ("MaxPower", ctypes.c_uint8),
        ("interface", ctypes.POINTER(Interface)),
        ("extra", ctypes.c_void_p),
        ("extra_length", ctypes.c_int),
    ]


_lib = None


def libusb():
    global _lib
    if _lib is not None:
        return _lib
    for path in LIBUSB_CANDIDATES:
        if not path:
            continue
        try:
            lib = ctypes.CDLL(path)
        except OSError:
            continue
        lib.libusb_init.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
        lib.libusb_exit.argtypes = [ctypes.c_void_p]
        lib.libusb_open_device_with_vid_pid.argtypes = [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_uint16]
        lib.libusb_open_device_with_vid_pid.restype = ctypes.c_void_p
        lib.libusb_get_device.argtypes = [ctypes.c_void_p]
        lib.libusb_get_device.restype = ctypes.c_void_p
        lib.libusb_get_active_config_descriptor.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.POINTER(ConfigDesc))]
        lib.libusb_free_config_descriptor.argtypes = [ctypes.POINTER(ConfigDesc)]
        lib.libusb_claim_interface.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.libusb_release_interface.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.libusb_close.argtypes = [ctypes.c_void_p]
        lib.libusb_control_transfer.argtypes = [
            ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint8, ctypes.c_uint16,
            ctypes.c_uint16, ctypes.c_void_p, ctypes.c_uint16, ctypes.c_uint,
        ]
        lib.libusb_bulk_transfer.argtypes = [
            ctypes.c_void_p, ctypes.c_uint8, ctypes.c_void_p, ctypes.c_int,
            ctypes.POINTER(ctypes.c_int), ctypes.c_uint,
        ]
        lib.libusb_error_name.argtypes = [ctypes.c_int]
        lib.libusb_error_name.restype = ctypes.c_char_p
        _lib = lib
        return lib
    raise P7Error("libusb-1.0 not found (try: brew install libusb)")


# --- Packet encoding -------------------------------------------------------

def escape(data):
    """0x5C padding: bytes < 0x20 become '\\' + (b + 0x20); '\\' becomes '\\\\'."""
    out = bytearray()
    for b in data:
        if b < 0x20:
            out += bytes((0x5C, b + 0x20))
        elif b == 0x5C:
            out += b"\\\\"
        else:
            out.append(b)
    return bytes(out)


def unescape(data):
    out = bytearray()
    it = iter(data)
    for b in it:
        if b == 0x5C:
            n = next(it)
            out.append(0x5C if n == 0x5C else n - 0x20)
        else:
            out.append(b)
    return bytes(out)


def checksum(body):
    """Two's complement of the byte sum from ST through D (or EX)."""
    return b"%02X" % ((-sum(body)) & 0xFF)


def encode_packet(ptype, subtype, data=None):
    if data is None:
        body = b"%02X0" % subtype
    else:
        enc = escape(data)
        body = b"%02X1%04X" % (subtype, len(enc)) + enc
    return bytes([ptype]) + body + checksum(body)


def command_payload(ow=0, dt=0, fs=0, fields=()):
    """Extended command payload: OW, DT, FS, SD1..SD6, then D1..D6."""
    fields = [f.encode("latin-1") if isinstance(f, str) else bytes(f) for f in fields]
    fields += [b""] * (6 - len(fields))
    return (
        b"%02X%02X%08X" % (ow, dt, fs)
        + b"".join(b"%02X" % len(f) for f in fields)
        + b"".join(fields)
    )


def parse_command_payload(d):
    ow, dt, fs = int(d[0:2], 16), int(d[2:4], 16), int(d[4:12], 16)
    sizes = [int(d[12 + 2 * i:14 + 2 * i], 16) for i in range(6)]
    fields, pos = [], 24
    for s in sizes:
        fields.append(d[pos:pos + s].decode("latin-1"))
        pos += s
    return {"ow": ow, "dt": dt, "fs": fs, "fields": fields}


class Packet:
    def __init__(self, ptype, subtype, data, extended):
        self.type, self.subtype, self.data, self.extended = ptype, subtype, data, extended

    def __repr__(self):
        return "Packet(type=0x%02X, st=%02X, %d bytes)" % (self.type, self.subtype, len(self.data))


# --- Sunplus SPCP8x5 USB-serial adapter (3-pin cable) ----------------------
# macOS has no driver for this chip, so it is driven from user space.
# Requests follow Linux drivers/usb/serial/spcp8x5.c.

SPCP8X5_IDS = ((0x04FC, 0x0201), (0x04FC, 0x0235), (0x04FC, 0x0231), (0x04FC, 0x0204), (0x0471, 0x081E))
SPCP8X5_BAUD = {300: 0x00, 600: 0x01, 1200: 0x02, 2400: 0x03, 4800: 0x04, 9600: 0x05,
                19200: 0x07, 38400: 0x09, 57600: 0x0A, 115200: 0x0B}
SPCP8X5_PARITY = {None: 0x00, "odd": 0x10, "even": 0x30}


def spcp8x5_format(baud, stop_bits=1, parity=None):
    """wValue for SET_UART_FORMAT (0x40/0x21): baud code, then 8 data bits | stop bits | parity."""
    fmt = 0x03 | (0x04 if stop_bits == 2 else 0x00) | SPCP8X5_PARITY[parity]
    return SPCP8X5_BAUD[baud] | (fmt << 8)


# --- Link ------------------------------------------------------------------

class Link:
    """One Protocol 7.00 session with a calculator in LINK > RECV mode.

    serial=False: the calculator's own USB port (LINK cable type USB).
    serial=True:  the 3-pin port through a SPCP8x5 USB-serial cable (cable type 3PIN),
                  9600 bauds, no parity, 2 stop bits as Protocol 7.00 starts on serial.
    """

    def __init__(self, timeout_ms=3000, verbose=False, serial=False, baud=9600, stop_bits=2):
        self.timeout_ms = timeout_ms
        self.verbose = verbose
        self.serial = serial
        self.baud = baud
        self.stop_bits = stop_bits
        self.lib = libusb()
        self.ctx = ctypes.c_void_p()
        self.handle = None
        self.rx = bytearray()
        if self.lib.libusb_init(ctypes.byref(self.ctx)) != 0:
            raise P7Error("libusb_init failed")

    # Context management -----------------------------------------------------

    def __enter__(self):
        if not self.handle:
            self.open()
        return self

    def __exit__(self, *exc):
        self.close()

    def _err(self, rc):
        return self.lib.libusb_error_name(rc).decode()

    def open(self):
        lib = self.lib
        ids = SPCP8X5_IDS if self.serial else [(CASIO_VID, pid) for pid in SEVEN_PIDS]
        for vid, pid in ids:
            self.handle = lib.libusb_open_device_with_vid_pid(self.ctx, vid, pid)
            if self.handle:
                break
        if not self.handle:
            raise P7Error("no USB-serial cable found" if self.serial else "no Casio calculator found on USB")

        cfg = ctypes.POINTER(ConfigDesc)()
        if lib.libusb_get_active_config_descriptor(lib.libusb_get_device(self.handle), ctypes.byref(cfg)) != 0:
            raise P7Error("could not read config descriptor")
        intf = cfg.contents.interface[0].altsetting[0]
        self.ep_in = self.ep_out = None
        for i in range(intf.bNumEndpoints):
            ep = intf.endpoint[i]
            if ep.bmAttributes & 3 != 2:  # bulk only
                continue
            if ep.bEndpointAddress & 0x80:
                self.ep_in = ep.bEndpointAddress
            else:
                self.ep_out = ep.bEndpointAddress
        lib.libusb_free_config_descriptor(cfg)
        if self.ep_in is None or self.ep_out is None:
            raise P7Error("bulk endpoints not found")

        rc = lib.libusb_claim_interface(self.handle, 0)
        if rc != 0:
            raise P7Error("claim_interface failed: %s" % self._err(rc))
        if self.serial:
            self.set_serial(self.baud, self.stop_bits)
        else:
            # Device enabling control flow (needed by some older models, harmless otherwise).
            lib.libusb_control_transfer(self.handle, 0x41, 0x01, 0, 0, None, 0, 300)

    def set_serial(self, baud, stop_bits=2, parity=None):
        rc = self.lib.libusb_control_transfer(self.handle, 0x40, 0x21, spcp8x5_format(baud, stop_bits, parity),
                                              0, None, 0, 300)
        if rc < 0:
            raise P7Error("could not set serial format: %s" % self._err(rc))
        self.baud, self.stop_bits = baud, stop_bits

    def read_raw(self, timeout_ms):
        """Whatever bytes arrive within timeout_ms (for sniffing non-Protocol-7 traffic)."""
        try:
            self._fill(1 << 30, timeout_ms)
        except P7Error:
            pass
        data, self.rx = bytes(self.rx), bytearray()
        return data

    def close(self):
        if self.handle:
            self.lib.libusb_release_interface(self.handle, 0)
            self.lib.libusb_close(self.handle)
            self.handle = None
        if self.ctx:
            self.lib.libusb_exit(self.ctx)
            self.ctx = None

    # Raw packets ------------------------------------------------------------

    def _write(self, data):
        buf = ctypes.create_string_buffer(data, len(data))
        done = ctypes.c_int(0)
        rc = self.lib.libusb_bulk_transfer(self.handle, self.ep_out, buf, len(data), ctypes.byref(done), self.timeout_ms)
        if rc != 0 or done.value != len(data):
            raise P7Error("bulk OUT failed: %s (%d/%d bytes)" % (self._err(rc), done.value, len(data)))

    def _fill(self, need, timeout_ms):
        buf = ctypes.create_string_buffer(512)
        done = ctypes.c_int(0)
        deadline = time.monotonic() + timeout_ms / 1000.0
        while len(self.rx) < need:
            left = max(1, int((deadline - time.monotonic()) * 1000))
            rc = self.lib.libusb_bulk_transfer(self.handle, self.ep_in, buf, 512, ctypes.byref(done), min(left, 1000))
            self.rx += buf.raw[: done.value]
            if rc == LIBUSB_ERROR_TIMEOUT:
                if time.monotonic() >= deadline:
                    raise P7Error("timed out waiting for calculator (have %d bytes)" % len(self.rx))
                continue
            if rc != 0:
                raise P7Error("bulk IN failed: %s" % self._err(rc))

    def send(self, ptype, subtype, data=None):
        raw = encode_packet(ptype, subtype, data)
        if self.verbose:
            print("  >> %r" % raw[:80])
        self._write(raw)

    def recv(self, timeout_ms=None):
        timeout_ms = timeout_ms or self.timeout_ms
        self._fill(4, timeout_ms)
        ptype, subtype, ext = self.rx[0], int(self.rx[1:3], 16), self.rx[3:4] == b"1"
        size = 6
        if ext:
            self._fill(8, timeout_ms)
            size += 4 + int(self.rx[4:8], 16)
        self._fill(size, timeout_ms)
        raw, self.rx = bytes(self.rx[:size]), self.rx[size:]
        if self.verbose:
            print("  << %r" % raw[:80])
        if checksum(raw[1:-2]) != raw[-2:].upper():
            raise P7Error("bad checksum in packet %r" % raw[:40])
        return Packet(ptype, subtype, unescape(raw[8:-2]) if ext else b"", ext)

    def expect_ack(self, what, timeout_ms=None):
        p = self.recv(timeout_ms)
        if p.type != T_ACK:
            raise P7Error("%s: expected ACK, got type 0x%02X subtype %02X" % (what, p.type, p.subtype))
        return p

    def ack(self):
        self.send(T_ACK, 0x00)

    # Session flows ----------------------------------------------------------

    def start(self):
        """Initial check; the calculator answers only when in LINK > RECV."""
        self.send(T_CHECK, 0x00)
        self.expect_ack("initial check")

    def terminate(self):
        self.send(T_TERMINATE, 0x00)
        try:
            self.expect_ack("terminate", 1500)
        except P7Error:
            pass

    def device_info(self):
        self.send(T_COMMAND, 0x01)
        p = self.recv()
        if p.type != T_ACK or p.subtype != 0x02:
            raise P7Error("expected extended ACK for device info")
        d = p.data

        def f(off, size):
            return d[off:off + size].rstrip(b"\xff\x00 ").decode("ascii", "replace")

        return {"hwid": f(0, 8), "os": f(96, 16), "product_id": f(132, 16)}

    def _receive_transfers(self, on_command):
        """After our roleswap, the calculator is active: ACK its commands and data until it swaps back."""
        while True:
            p = self.recv(10000)
            if p.type == T_ROLESWAP:
                return
            if p.type == T_COMMAND:
                on_command(p)
            elif p.type == T_TERMINATE:
                self.ack()
                raise P7Error("calculator terminated the link")
            else:
                raise P7Error("unexpected packet during transfer: %r" % p)

    def list_mcs(self):
        """Command 2D: information about every main memory file."""
        self.send(T_COMMAND, 0x2D)
        self.expect_ack("request file info")
        self.send(T_ROLESWAP, 0x00)
        files = []

        def on_command(p):
            if p.subtype == 0x2E:
                c = parse_command_payload(p.data)
                files.append({"dt": c["dt"], "size": c["fs"], "dir": c["fields"][0],
                              "name": c["fields"][1], "group": c["fields"][2]})
            self.ack()

        self._receive_transfers(on_command)
        return files

    def _read_data_packets(self, subtype):
        chunks = []
        while True:
            p = self.recv(10000)
            if p.type != T_DATA or p.subtype != subtype:
                raise P7Error("expected data packet, got %r" % p)
            total, current = int(p.data[0:4], 16), int(p.data[4:8], 16)
            chunks.append(p.data[8:])
            self.ack()
            if current >= total:
                return b"".join(chunks)

    def get_mcs(self, dt, directory, name, group):
        """Command 24: fetch one main memory file. Returns (header, content)."""
        self.send(T_COMMAND, 0x24, command_payload(0, dt, 0, [directory, name, group]))
        p = self.recv()
        if p.type == T_NAK:
            raise P7Error("calculator refused request for %s/%s/%s (NAK %02X)" % (group, directory, name, p.subtype))
        if p.type != T_ACK:
            raise P7Error("unexpected reply to file request: %r" % p)
        self.send(T_ROLESWAP, 0x00)
        result = {}

        def on_command(p):
            if p.subtype != 0x25:
                self.ack()
                return
            c = parse_command_payload(p.data)
            result["header"] = c
            self.ack()
            result["content"] = self._read_data_packets(0x25) if c["fs"] else b""

        self._receive_transfers(on_command)
        if "header" not in result:
            raise P7Error("calculator sent no file")
        return result["header"], result["content"]

    def get_all_mcs(self):
        """Command 29: the calculator sends every main memory file (read-only backup).

        Note the neighbouring command 2A resets the main memory; never send it.
        """
        self.send(T_COMMAND, 0x29)
        self.expect_ack("request all files")
        self.send(T_ROLESWAP, 0x00)
        files = []

        def on_command(p):
            if p.subtype != 0x25:
                self.ack()
                return
            c = parse_command_payload(p.data)
            self.ack()
            content = self._read_data_packets(0x25) if c["fs"] else b""
            files.append({"dt": c["dt"], "size": c["fs"], "dir": c["fields"][0], "name": c["fields"][1],
                          "group": c["fields"][2], "content": content})

        self._receive_transfers(on_command)
        return files

    def _put_file(self, command, dt, fields, content, overwrite, label, progress=None):
        ow = 0x02 if overwrite else 0x00
        self.send(T_COMMAND, command, command_payload(ow, dt, len(content), fields))
        p = self.recv()
        if p.type == T_NAK and p.subtype == 0x02:  # overwrite confirmation requested
            self.send(T_ACK, 0x01)
            p = self.recv()
        if p.type != T_ACK:
            raise P7Error("calculator refused %s (type 0x%02X subtype %02X)" % (label, p.type, p.subtype))
        chunks = [content[i:i + 256] for i in range(0, len(content), 256)] or [b""]
        for i, chunk in enumerate(chunks, 1):
            self.send(T_DATA, command, b"%04X%04X" % (len(chunks), i) + chunk)
            self.expect_ack("data packet %d/%d" % (i, len(chunks)), 10000)
            if progress:
                progress(i, len(chunks))

    def put_mcs(self, dt, directory, name, group, content, overwrite=True):
        """Command 25: write one main memory file (force overwrite by default)."""
        self._put_file(0x25, dt, [directory, name, group], content, overwrite, "file %s/%s" % (group, name))

    def put_storage(self, name, content, device="fls0", directory="", overwrite=True, progress=None):
        """Command 45: write a file (e.g. an add-in .g1a) to storage memory."""
        self._put_file(0x45, 0x00, [directory, name, "", "", device], content, overwrite,
                       "storage file %s on %s" % (name, device), progress)

    def list_storage(self, device="fls0"):
        """Command 4D: information about every file on a storage device (answered with 4E commands)."""
        self.send(T_COMMAND, 0x4D, command_payload(0, 0, 0, ["", "", "", "", device]))
        self.expect_ack("request storage file info")
        self.send(T_ROLESWAP, 0x00)
        files = []

        def on_command(p):
            if p.subtype == 0x4E:
                c = parse_command_payload(p.data)
                files.append({"dir": c["fields"][0], "name": c["fields"][1], "size": c["fs"]})
            self.ack()

        self._receive_transfers(on_command)
        return files


def wait_for_calculator(poll_s=1.0, timeout_ms=1500, verbose=False, serial=False):
    """Block until a calculator in LINK > RECV answers the initial check; return an open Link."""
    while True:
        link = Link(timeout_ms=timeout_ms, verbose=verbose, serial=serial)
        try:
            link.open()
            link.start()
            link.timeout_ms = 5000
            return link
        except P7Error:
            link.close()
            time.sleep(poll_s)
