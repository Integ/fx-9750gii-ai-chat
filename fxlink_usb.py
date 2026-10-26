"""fxlink protocol over USB, as spoken by gint add-ins (libusb via ctypes, no other dependencies).

A gint add-in shows up as 07cf:6101 like the OS in LINK mode, but with a vendor
interface of subclass 0x77. Every message is a 48-byte little-endian header
(version 0x00000100, size, transfer_size, application[16], type[16]) followed by
`size` bytes of content. Like the fxSDK's fxlink, the host sends the header and
the content as two bulk transfers, each ending in a short or zero-length packet.

Reference: gint include/gint/usb-ff-bulk.h, fxSDK libfxlink/devices.c.
"""

import ctypes
import struct
import time

from p7link import CASIO_VID, LIBUSB_ERROR_TIMEOUT, ConfigDesc, libusb

HEADER = struct.Struct("<III16s16s")
VERSION = 0x00000100
GINT_SUBCLASS = 0x77
FULL_SPEED_PACKET = 64
LIBUSB_ERROR_NO_DEVICE = -4


class FxlinkError(Exception):
    pass


class Message:
    def __init__(self, application, type, data):
        self.application, self.type, self.data = application, type, data

    def __repr__(self):
        return "Message(%s.%s, %d bytes)" % (self.application, self.type, len(self.data))


def _name(raw):
    return raw.split(b"\x00", 1)[0].decode("utf-8", "replace")


class FxlinkDevice:
    def __init__(self, verbose=False):
        self.verbose = verbose
        self.lib = libusb()
        self.ctx = ctypes.c_void_p()
        self.handle = None
        self.rx = bytearray()
        if self.lib.libusb_init(ctypes.byref(self.ctx)) != 0:
            raise FxlinkError("libusb_init failed")

    def _err(self, rc):
        return self.lib.libusb_error_name(rc).decode()

    def open(self):
        """Open a calculator running a gint add-in with USB enabled; FxlinkError if there is none."""
        lib = self.lib
        handle = lib.libusb_open_device_with_vid_pid(self.ctx, CASIO_VID, 0x6101)
        if not handle:
            raise FxlinkError("no calculator on USB")
        cfg = ctypes.POINTER(ConfigDesc)()
        if lib.libusb_get_active_config_descriptor(lib.libusb_get_device(handle), ctypes.byref(cfg)) != 0:
            lib.libusb_close(handle)
            raise FxlinkError("could not read config descriptor")
        found = None
        for i in range(cfg.contents.bNumInterfaces):
            alt = cfg.contents.interface[i].altsetting[0]
            if alt.bInterfaceClass == 0xFF and alt.bInterfaceSubClass == GINT_SUBCLASS:
                eps = [alt.endpoint[j] for j in range(alt.bNumEndpoints)]
                ep_in = next(e.bEndpointAddress for e in eps if e.bmAttributes & 3 == 2 and e.bEndpointAddress & 0x80)
                ep_out = next(e.bEndpointAddress for e in eps if e.bmAttributes & 3 == 2 and not e.bEndpointAddress & 0x80)
                found = (alt.bInterfaceNumber, ep_in, ep_out)
                break
        lib.libusb_free_config_descriptor(cfg)
        if found is None:
            lib.libusb_close(handle)
            raise FxlinkError("calculator found, but it is not running a gint add-in with USB")
        self.interface, self.ep_in, self.ep_out = found
        rc = lib.libusb_claim_interface(handle, self.interface)
        if rc != 0:
            lib.libusb_close(handle)
            raise FxlinkError("claim_interface failed: %s" % self._err(rc))
        self.handle = handle
        self.rx = bytearray()

    def close(self):
        if self.handle:
            self.lib.libusb_release_interface(self.handle, self.interface)
            self.lib.libusb_close(self.handle)
            self.handle = None

    def __enter__(self):
        if not self.handle:
            self.open()
        return self

    def __exit__(self, *exc):
        self.close()

    # Raw transfers -----------------------------------------------------------

    def _bulk_out(self, data, timeout_ms=5000):
        buf = ctypes.create_string_buffer(bytes(data), max(1, len(data)))
        done = ctypes.c_int(0)
        rc = self.lib.libusb_bulk_transfer(self.handle, self.ep_out, buf, len(data), ctypes.byref(done), timeout_ms)
        if rc != 0 or done.value != len(data):
            raise FxlinkError("bulk OUT failed: %s (%d/%d bytes)" % (self._err(rc), done.value, len(data)))

    def _write_transaction(self, data):
        self._bulk_out(data)
        if len(data) % FULL_SPEED_PACKET == 0:
            self._bulk_out(b"")  # zero-length packet ends the transaction

    def _fill(self, need, timeout_ms):
        buf = ctypes.create_string_buffer(4096)
        done = ctypes.c_int(0)
        deadline = time.monotonic() + timeout_ms / 1000.0
        while len(self.rx) < need:
            left = int((deadline - time.monotonic()) * 1000)
            if left <= 0:
                return False
            rc = self.lib.libusb_bulk_transfer(self.handle, self.ep_in, buf, 4096, ctypes.byref(done), min(left, 500))
            self.rx += buf.raw[: done.value]
            if rc == LIBUSB_ERROR_TIMEOUT:
                continue
            if rc == LIBUSB_ERROR_NO_DEVICE:
                raise FxlinkError("calculator disconnected")
            if rc != 0:
                raise FxlinkError("bulk IN failed: %s" % self._err(rc))
        return True

    # Messages ----------------------------------------------------------------

    def send(self, application, type, data=b""):
        if isinstance(data, str):
            data = data.encode("utf-8")
        header = HEADER.pack(VERSION, len(data), 0, application.encode()[:16], type.encode()[:16])
        if self.verbose:
            print("  >> %s.%s %d bytes" % (application, type, len(data)))
        self._write_transaction(header)
        if data:
            self._write_transaction(data)

    def recv(self, timeout_ms=500):
        """Next message from the calculator, or None if nothing arrived within timeout_ms."""
        if not self._fill(HEADER.size, timeout_ms):
            return None
        version, size, _, app, typ = HEADER.unpack(bytes(self.rx[:HEADER.size]))
        if version != VERSION:
            self.rx = bytearray()
            raise FxlinkError("bad fxlink header (version %08x)" % version)
        if not self._fill(HEADER.size + size, max(timeout_ms, 5000)):
            raise FxlinkError("message %s.%s truncated" % (_name(app), _name(typ)))
        data = bytes(self.rx[HEADER.size:HEADER.size + size])
        self.rx = self.rx[HEADER.size + size:]
        msg = Message(_name(app), _name(typ), data)
        if self.verbose:
            print("  << %r" % msg)
        return msg
