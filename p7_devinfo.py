#!/usr/bin/env python3
"""Read-only device-info query for Casio fx-9860G-family calculators over USB.

Speaks just enough Casio Protocol 7.00 to ask the calculator who it is:
    initial check (05 00) -> get device info (01 01) -> terminate (18 00)
Nothing is written to the calculator's memory.

Requires libusb-1.0 (e.g. `brew install libusb`). Put the calculator in
MENU > LINK, cable = USB, then RECV before running.

Protocol reference: https://cahuteproject.org/topics/protocols/seven/packet-format.html
"""

import ctypes
import ctypes.util
import sys

CASIO_VID = 0x07CF
SEVEN_PIDS = (0x6101, 0x6102)
TIMEOUT_MS = 3000
LIBUSB_ERROR_TIMEOUT = -7

LIBUSB_CANDIDATES = (
    "/opt/homebrew/lib/libusb-1.0.dylib",
    "/usr/local/lib/libusb-1.0.dylib",
    ctypes.util.find_library("usb-1.0"),
)


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


def load_libusb():
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
        return lib
    sys.exit("libusb-1.0 not found (try: brew install libusb)")


def checksum(body):
    """Two's complement of the byte sum from ST through D (or EX)."""
    return b"%02X" % ((-sum(body)) & 0xFF)


def make_packet(ptype, subtype):
    body = b"%02X" % subtype + b"0"
    return bytes([ptype]) + body + checksum(body)


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


class Link:
    def __init__(self, lib, handle, ep_in, ep_out):
        self.lib, self.handle, self.ep_in, self.ep_out = lib, handle, ep_in, ep_out
        self.rx = bytearray()

    def err(self, rc):
        return self.lib.libusb_error_name(rc).decode()

    def send(self, data):
        buf = ctypes.create_string_buffer(data, len(data))
        done = ctypes.c_int(0)
        rc = self.lib.libusb_bulk_transfer(self.handle, self.ep_out, buf, len(data), ctypes.byref(done), TIMEOUT_MS)
        if rc != 0 or done.value != len(data):
            raise IOError("bulk OUT failed: %s (%d/%d bytes)" % (self.err(rc), done.value, len(data)))

    def _fill(self, need):
        buf = ctypes.create_string_buffer(512)
        done = ctypes.c_int(0)
        while len(self.rx) < need:
            rc = self.lib.libusb_bulk_transfer(self.handle, self.ep_in, buf, 512, ctypes.byref(done), TIMEOUT_MS)
            if rc != 0 and not (rc == LIBUSB_ERROR_TIMEOUT and done.value):
                raise IOError("bulk IN failed: %s (have %d bytes: %r)" % (self.err(rc), len(self.rx), bytes(self.rx)))
            self.rx += buf.raw[: done.value]

    def recv_packet(self):
        self._fill(4)
        ptype, subtype, ext = self.rx[0], int(self.rx[1:3], 16), self.rx[3:4] == b"1"
        size = 4 + 2
        if ext:
            self._fill(8)
            size += 4 + int(self.rx[4:8], 16)
        self._fill(size)
        raw, self.rx = bytes(self.rx[:size]), self.rx[size:]
        if checksum(raw[1:-2]) != raw[-2:].upper():
            raise IOError("bad checksum in packet %r" % raw)
        data = unescape(raw[8:-2]) if ext else b""
        return ptype, subtype, data


def field(data, off, size):
    return data[off:off + size].rstrip(b"\xff\x00 ").decode("ascii", "replace")


def main():
    lib = load_libusb()
    ctx = ctypes.c_void_p()
    if lib.libusb_init(ctypes.byref(ctx)) != 0:
        sys.exit("libusb_init failed")

    handle = None
    for pid in SEVEN_PIDS:
        handle = lib.libusb_open_device_with_vid_pid(ctx, CASIO_VID, pid)
        if handle:
            print("Found Casio device %04x:%04x" % (CASIO_VID, pid))
            break
    if not handle:
        lib.libusb_exit(ctx)
        sys.exit("No Casio Protocol 7.00 device found (is the calculator in LINK > RECV?)")

    cfg = ctypes.POINTER(ConfigDesc)()
    if lib.libusb_get_active_config_descriptor(lib.libusb_get_device(handle), ctypes.byref(cfg)) != 0:
        sys.exit("could not read config descriptor")
    intf = cfg.contents.interface[0].altsetting[0]
    ep_in = ep_out = None
    for i in range(intf.bNumEndpoints):
        ep = intf.endpoint[i]
        if ep.bmAttributes & 3 != 2:  # bulk only
            continue
        if ep.bEndpointAddress & 0x80:
            ep_in = ep.bEndpointAddress
        else:
            ep_out = ep.bEndpointAddress
    lib.libusb_free_config_descriptor(cfg)
    if ep_in is None or ep_out is None:
        sys.exit("bulk endpoints not found")
    print("Bulk IN 0x%02x, bulk OUT 0x%02x" % (ep_in, ep_out))

    rc = lib.libusb_claim_interface(handle, 0)
    if rc != 0:
        sys.exit("claim_interface failed: %s" % lib.libusb_error_name(rc).decode())

    try:
        # Device enabling control flow (needed by some older models, harmless otherwise).
        lib.libusb_control_transfer(handle, 0x41, 0x01, 0, 0, None, 0, 300)

        link = Link(lib, handle, ep_in, ep_out)

        link.send(make_packet(0x05, 0x00))  # initial check
        t, st, _ = link.recv_packet()
        if t != 0x06:
            raise IOError("initial check not acknowledged (type 0x%02x subtype %02x)" % (t, st))

        link.send(make_packet(0x01, 0x01))  # command 01: get device info
        t, st, info = link.recv_packet()
        if t != 0x06 or st != 0x02:
            raise IOError("expected extended ACK, got type 0x%02x subtype %02x" % (t, st))

        link.send(make_packet(0x18, 0x00))  # terminate
        try:
            link.recv_packet()
        except IOError:
            pass
    finally:
        lib.libusb_release_interface(handle, 0)
        lib.libusb_close(handle)
        lib.libusb_exit(ctx)

    print("Payload: %d bytes" % len(info))
    rows = [
        ("Hardware ID", 0, 8),
        ("Processor ID", 8, 16),
        ("Preprogrammed ROM (KB)", 24, 8),
        ("Flash ROM (KB)", 32, 8),
        ("RAM (KB)", 40, 8),
        ("Preprogrammed ROM version", 48, 16),
        ("Bootcode version", 64, 16),
        ("Bootcode offset", 80, 8),
        ("Bootcode size", 88, 8),
        ("OS version", 96, 16),
        ("OS offset", 112, 8),
        ("OS size", 120, 8),
        ("Protocol version", 128, 4),
        ("Product ID", 132, 16),
        ("User name", 148, 16),
    ]
    for name, off, size in rows:
        print("  %-26s %s" % (name + ":", field(info, off, size)))


if __name__ == "__main__":
    main()
