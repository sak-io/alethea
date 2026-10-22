"""Fail any socket connect to a non-loopback address (R1)."""
import ipaddress
import socket
from unittest import mock

_orig = socket.socket.connect


def _is_loopback(addr):
    if isinstance(addr, (str, bytes)):  # AF_UNIX path
        return True
    host = addr[0]
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _guarded(self, addr):
    if not _is_loopback(addr):
        raise AssertionError("non-loopback connect blocked: %r" % (addr,))
    return _orig(self, addr)


def patch():
    """Return a started patcher; call .stop() to undo."""
    p = mock.patch.object(socket.socket, "connect", _guarded)
    p.start()
    return p
