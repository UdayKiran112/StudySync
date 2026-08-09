"""
bonjour_mdns.py
---------------
Publish the `studysync.local` hostname through the machine's *already-running*
Apple Bonjour responder instead of running a second mDNS responder of our own.

Why this exists
---------------
StudySync also advertises studysync.local with a small zeroconf-based responder
(see run_server._advertise_mdns_via_zeroconf). That works when nothing else owns
UDP port 5353, but Apple's Bonjour Service (mDNSResponder.exe) is running on many
Windows machines -- installed by iTunes, printers, or the venue admin. Two mDNS
responders on one interface fight over 5353 and break the name.

The fix is to use the *client* side of the responder that is already there. This
module loads the Bonjour client API (dnssd.dll) and asks Apple's mDNSResponder to
publish an A record for `studysync.local`. mDNSResponder does the multicast
announcements and answers queries, so:

  * Bonjour Service keeps running and keeps owning UDP 5353 (no conflict),
  * every device that speaks mDNS (iPhone/Android/Mac, Windows with Bonjour or
    the built-in mDNS) resolves http://studysync.local to this machine,
  * when no Bonjour install is present, load_bonjour_client() returns None and
    the caller falls back to its own responder.

The A-record path (DNSServiceRegisterRecord) is used instead of
DNSServiceRegisterHostname because the dnssd.dll shipped with some Bonjour
installs does not export the hostname convenience function, but always exports
the raw record API.
"""

import ctypes
import logging
import socket
import sys

logger = logging.getLogger("studysync.mdns")

# --- Bonjour constants (dnssd.h) ---
_DNSServiceType_A = 1
_DNSServiceClass_IN = 1
_DNSServiceFlagsUnique = 0x20
_DNSServiceFlagsNoAutoRename = 0x8
_kDNSServiceErr_NoError = 0
_kDNSServiceErr_ServiceNotRunning = -65562

# void (*DNSServiceRegisterRecordReply)(DNSServiceRef sdRef,
#     DNSRecordRef RecordRef, DNSServiceFlags flags,
#     DNSServiceErrorType errorCode, void *context)
_Reply = ctypes.CFUNCTYPE(
    None,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_uint32,
    ctypes.c_int,
    ctypes.c_void_p,
)


def _on_reply(sd_ref, record_ref, flags, error_code, context):
    if error_code != _kDNSServiceErr_NoError:
        logger.warning("mDNS: Bonjour record registration error: %d", error_code)


# Keep strong references so callbacks / RecordRefs are not garbage-collected
# while their connection is alive.
_reply_cb = _Reply(_on_reply)
_active_record_refs: list[ctypes.c_void_p] = []


def load_bonjour_client():
    """Return a ctypes handle to the Bonjour client API, or None if this
    machine has no Bonjour install (no dnssd.dll on the load path)."""
    if not sys.platform.startswith("win"):
        return None
    try:
        lib = ctypes.CDLL("dnssd.dll")
    except (OSError, ValueError):
        return None
    try:
        lib.DNSServiceCreateConnection.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
        lib.DNSServiceCreateConnection.restype = ctypes.c_int
        lib.DNSServiceRefSockFD.argtypes = [ctypes.c_void_p]
        lib.DNSServiceRefSockFD.restype = ctypes.c_int
        lib.DNSServiceRegisterRecord.argtypes = [
            ctypes.c_void_p,  # sdRef
            ctypes.POINTER(ctypes.c_void_p),  # RecordRef (out)
            ctypes.c_uint32,  # flags (must include Unique/Shared/KnownUnique)
            ctypes.c_uint32,  # interfaceIndex (0 = all interfaces)
            ctypes.c_char_p,  # fullname, e.g. b"studysync.local."
            ctypes.c_uint16,  # rrtype (A = 1)
            ctypes.c_uint16,  # rrclass (IN = 1)
            ctypes.c_uint16,  # rdlen (4 for an A record)
            ctypes.c_void_p,  # rdata (packed 4-byte IPv4)
            ctypes.c_uint32,  # ttl (0 = default; 240 = standard mDNS)
            _Reply,  # callBack
            ctypes.c_void_p,  # context
        ]
        lib.DNSServiceRegisterRecord.restype = ctypes.c_int
        lib.DNSServiceProcessResult.argtypes = [ctypes.c_void_p]
        lib.DNSServiceProcessResult.restype = ctypes.c_int
        lib.DNSServiceRefDeallocate.argtypes = [ctypes.c_void_p]
        lib.DNSServiceRefDeallocate.restype = None
    except (AttributeError, TypeError):
        return None
    return lib


def register_hostname_records(client, addrs, hostname="studysync.local."):
    """Ask the running Bonjour responder to publish an A record for `hostname`
    for each IPv4 in `addrs`.

    Returns an opaque connection handle that MUST be kept alive (and serviced
    with process_events()) for the records to stay published, or None on
    failure. Call DNSServiceRefDeallocate on the handle to unpublish.
    """
    if not addrs:
        return None
    conn = ctypes.c_void_p()
    err = client.DNSServiceCreateConnection(ctypes.byref(conn))
    if err != _kDNSServiceErr_NoError or not conn.value:
        if err == _kDNSServiceErr_ServiceNotRunning:
            logger.debug("mDNS: Bonjour responder not running (err %d)", err)
        else:
            logger.warning("mDNS: Bonjour create connection failed: err %d", err)
        return None
    for ip in addrs:
        try:
            packed = socket.inet_aton(ip)
        except OSError:
            logger.warning("mDNS: skipping unparseable address %r", ip)
            continue
        rdata = ctypes.create_string_buffer(packed, 4)
        record_ref = ctypes.c_void_p()
        err = client.DNSServiceRegisterRecord(
            conn,
            ctypes.byref(record_ref),
            _DNSServiceFlagsUnique | _DNSServiceFlagsNoAutoRename,
            0,
            hostname.encode("ascii"),
            _DNSServiceType_A,
            _DNSServiceClass_IN,
            4,
            ctypes.cast(rdata, ctypes.c_void_p),
            240,  # ttl
            _reply_cb,
            None,
        )
        if err != _kDNSServiceErr_NoError:
            logger.warning("mDNS: Bonjour register record failed: err %d", err)
            client.DNSServiceRefDeallocate(conn)
            return None
        # Keep the RecordRef alive for the lifetime of the connection: the
        # daemon publishes the record as long as this connection is open.
        _active_record_refs.append(record_ref)
    return conn


def process_events(client, conn):
    """Drain the Bonjour connection's socket so the responder can deliver
    events (record conflicts, etc.) without blocking on a full socket."""
    import select

    fd = client.DNSServiceRefSockFD(conn)
    if fd < 0:
        return
    try:
        while True:
            ready, _, _ = select.select([fd], [], [], 0)
            if not ready:
                break
            err = client.DNSServiceProcessResult(conn)
            if err != _kDNSServiceErr_NoError:
                logger.warning("mDNS: Bonjour process result failed: err %d", err)
                break
    except OSError:
        pass
