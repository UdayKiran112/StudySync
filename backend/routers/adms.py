"""
routers/adms.py
------------------
A real ZKTeco ADMS server: the /iclock/* endpoints the device itself
talks to when its Comm > Cloud Server Setting (ADMS) is pointed at us.

This is a push model -- the DEVICE opens the HTTP connection to US, on
whatever schedule its firmware decides, and we just answer. We never
connect out to the device (that's what the pyzk-based zkteco/ package
does instead; this module has no pyzk import at all).

Protocol summary (confirmed against ZKTeco's HTTP Push SDK spec and
real device captures):

  GET  /iclock/cdata?SN=...&options=all&...
       "Handshake". Sent once when the device (re)connects, before it
       sends any data. We answer with a small text config block telling
       it how often to check in and that we want real-time pushes
       (Realtime=1) rather than batched/scheduled ones.

  POST /iclock/cdata?SN=...&table=ATTLOG&Stamp=...
       The actual attendance push -- one or more tab-separated ATTLOG
       lines in the body, sent the moment a punch happens (because we
       asked for Realtime=1 above). This is the one that writes to the
       attendance table; see adms/ingest.py.

  POST /iclock/cdata?SN=...&table=OPERLOG   (or USER, etc.)
       User/operation-log data pushed from the device (e.g. new
       enrolments made directly on the device's keypad). We do not
       consume this -- our student roster lives in OUR database and is
       the source of truth, not the device's -- so we just acknowledge
       it. If you ever want two-way user sync this is where you'd add
       it, but it's out of scope for "read attendance punches".

  GET  /iclock/getrequest?SN=...
       Heartbeat / "do you have a command queued for me?" poll, called
       every Delay seconds. We never queue commands, so we always answer
       "OK" (no pending command).

  POST /iclock/devicecmd?SN=...
       Result of a command the device executed. We never send commands,
       so this should rarely fire, but we acknowledge it defensively in
       case a stale command is still queued in the device's own memory
       from a previous integration.

  GET/POST /iclock/fdata, GET/POST /iclock/test
       Photo/biometric-template push and a bare connectivity check some
       tools use. Acknowledged, not processed -- irrelevant to
       attendance.

WHY THESE ROUTES HAVE NO Depends(require_api_key)
----------------------------------------------------
ADMS has no authentication in the protocol -- the device cannot send a
custom header or API key, it just POSTs to whatever host/port you typed
into its menu. That means these routes are unauthenticated BY NECESSITY,
which is also why they are NOT mounted under /api like every other
router in this app (a device push protocol occupying /api/* would be
misleading). Two things stand in for auth instead:

  1. adms.config.adms_allowed_serials() -- an allowlist of the device's
     own serial number, which it sends as the "SN" query parameter on
     every single request. Requests from an unrecognised serial are
     logged and their data dropped, but we still answer "OK" (see
     _serial_allowed below for why).
  2. Network placement -- put this behind a firewall / on the same
     trusted LAN segment as the device, exactly like the pyzk protocol's
     own docs already warn about. Do not expose /iclock/* to the open
     internet.

WHY WE ALWAYS RETURN "OK" EVEN WHEN WE REJECT SOMETHING
------------------------------------------------------------
Returning anything other than a 200 with the literal body "OK" makes
ZKTeco firmware treat the push as a failed delivery: it will re-queue and
retry the same data on the next ErrorDelay cycle, potentially forever.
So rejections (bad serial, oversized body, unparseable payload) are
logged loudly server-side but still acknowledged to the device, which
just means "don't keep re-sending this" -- the alternative is a device
stuck in a retry loop that never resolves on its own.
"""

import logging

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse

from adms.config import (
    adms_allowed_serials,
    adms_delay_seconds,
    adms_error_delay_seconds,
)
from adms.ingest import (
    get_sync_status,
    ingest_attlog,
    note_handshake,
    note_heartbeat,
)
from models.adms import AdmsStatus
from security import require_api_key

logger = logging.getLogger("adms.router")

# Device-facing endpoints. No prefix beyond /iclock (fixed by the ZKTeco
# firmware, not configurable), no API-key dependency (see docstring).
router = APIRouter(prefix="/iclock", tags=["ADMS (device push)"])

# A staff-facing diagnostic endpoint, API-key protected like the rest of
# the app, kept in a separate router object so it can live under /api.
status_router = APIRouter(
    prefix="/api/adms",
    tags=["ADMS (device push)"],
    dependencies=[Depends(require_api_key)],
)

# ATTLOG batches are a handful of tab-separated lines; this is generous
# headroom while still bounding worst-case memory use from a malicious or
# malfunctioning sender on the LAN.
_MAX_BODY_BYTES = 2_000_000


def _serial_allowed(sn: str) -> bool:
    allowed = adms_allowed_serials()
    if not allowed:
        return True  # no allowlist configured -- see adms/config.py docstring
    return sn in allowed


async def _read_body_capped(request: Request) -> bytes:
    """Read the request body, refusing to buffer more than _MAX_BODY_BYTES."""
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > _MAX_BODY_BYTES:
                logger.warning(
                    "ADMS push body too large (%s bytes, Content-Length) -- dropping.",
                    content_length,
                )
                return b""
        except ValueError:
            pass
    body = await request.body()
    if len(body) > _MAX_BODY_BYTES:
        logger.warning("ADMS push body too large (%d bytes) -- truncating.", len(body))
        return body[:_MAX_BODY_BYTES]
    return body


@router.get("/cdata")
async def adms_handshake(SN: str = Query(default="")):
    """
    Initial handshake: device asks for its config, we tell it to push
    attendance in real time. Every field below is read by real ZKTeco
    firmware at connect time -- do not rename or drop any of them, some
    firmwares are picky about exactly which keys are present even if
    they don't need every value.
    """
    if not SN:
        logger.warning(
            "ADMS handshake with no SN query param -- malformed request, ignoring."
        )
        return PlainTextResponse("OK")

    if not _serial_allowed(SN):
        logger.warning("ADMS handshake from unrecognised serial SN=%s -- ignoring.", SN)
        return PlainTextResponse("OK")

    note_handshake(SN)
    logger.info("ADMS handshake from SN=%s", SN)

    lines = [
        f"GET OPTION FROM:{SN}",
        "Stamp=9999",
        "OpStamp=9999",
        "ATTLOGStamp=None",
        "OPERLOGStamp=9999",
        f"ErrorDelay={adms_error_delay_seconds()}",
        f"Delay={adms_delay_seconds()}",
        "TransTimes=00:00;14:05",
        "TransInterval=1",
        "TransFlag=1111000000",
        "TimeZone=0",
        # The whole point of ADMS over a poller: push the instant a swipe
        # happens instead of waiting for TransTimes/TransInterval.
        "Realtime=1",
        "Encrypt=0",
    ]
    return PlainTextResponse("\n".join(lines) + "\n")


@router.post("/cdata")
async def adms_data_push(
    request: Request,
    SN: str = Query(default=""),
    table: str = Query(default=""),
):
    """
    Data push. table=ATTLOG is the one we care about (see adms/ingest.py
    for what happens to it). Everything else is acknowledged and
    discarded -- see the module docstring for why.
    """
    body_bytes = await _read_body_capped(request)
    body = body_bytes.decode("utf-8", errors="replace")

    if not SN:
        logger.warning(
            "ADMS push with no SN query param -- malformed request, ignoring."
        )
        return PlainTextResponse("OK")

    if not _serial_allowed(SN):
        logger.warning(
            "ADMS push rejected: unrecognised device serial SN=%s (table=%s, %d bytes)",
            SN,
            table,
            len(body_bytes),
        )
        return PlainTextResponse("OK")

    table_upper = table.strip().upper()
    if table_upper == "ATTLOG":
        if body.strip():
            try:
                ingest_attlog(SN, body)
            except Exception:
                # Already logged with a traceback inside ingest_attlog.
                # Still ack the device -- see module docstring on retries.
                pass
    else:
        logger.info(
            "ADMS push acknowledged but not processed (table=%s, SN=%s, %d bytes) -- "
            "this integration only consumes ATTLOG.",
            table or "(none)",
            SN,
            len(body_bytes),
        )

    return PlainTextResponse("OK")


@router.get("/getrequest")
async def adms_getrequest(SN: str = Query(default="")):
    """Heartbeat / command poll. We never queue commands -> always 'OK'."""
    if SN:
        note_heartbeat(SN)
    return PlainTextResponse("OK")


@router.post("/devicecmd")
async def adms_devicecmd(request: Request, SN: str = Query(default="")):
    """Device reporting the result of a command we never sent it. Just ack."""
    body = (await _read_body_capped(request)).decode("utf-8", errors="replace")
    if body.strip():
        logger.info("ADMS devicecmd ack from SN=%s: %s", SN or "(none)", body.strip())
    return PlainTextResponse("OK")


@router.api_route("/fdata", methods=["GET", "POST"])
async def adms_fdata():
    """Photo/biometric-template push. Not processed by this integration."""
    return PlainTextResponse("OK")


@router.api_route("/test", methods=["GET", "POST"])
async def adms_test():
    """Bare connectivity check used by some ZKTeco setup tools."""
    return PlainTextResponse("OK")


@status_router.get("/status", response_model=AdmsStatus)
def adms_status():
    """
    Staff-facing (API-key protected) view of every device SN this server
    has seen: durable sync health from the device_state table (last
    reconcile, buffer size, ledger pending -- survives restarts) merged
    with the in-memory liveness view (handshake/heartbeat/push timestamps
    since startup). Useful for confirming a physical MB360 punch actually
    reached this server and that the ledger is keeping up, without having
    to read the raw application logs.
    """
    return AdmsStatus(devices=get_sync_status())
