"""
adms/config.py
----------------
Server-side configuration for the ZKTeco ADMS push endpoints.

Unlike zkteco/config.py (which configures where WE connect to reach the
device), there is no "device IP" to configure here -- ADMS is a push
protocol, so the device connects to US at whatever address/port you tell
it in its own Comm > Cloud Server Setting menu. Everything below only
tunes how our server answers once the device shows up.

Environment variables:

    ZK_ADMS_ALLOWED_SERIALS
                        Comma-separated list of device serial numbers
                        (the "SN" the device sends on every request)
                        allowed to push data in. Default: empty, meaning
                        "accept any serial".

                        IMPORTANT: the ADMS protocol has no
                        authentication at all -- the device can't send an
                        API key or a signed request, so /iclock/* must be
                        mounted without one. That means ANYTHING that can
                        reach this port can POST a fabricated ATTLOG batch
                        with any PIN it likes and have it written to the
                        attendance table as if a real student swiped in.
                        Set this env var once you know your device's real
                        serial (Menu > System Info > Device Info on the
                        MB360, or read it off the sticker on the back) so
                        requests claiming any other serial are logged and
                        dropped. Firewalling /iclock/* to your LAN segment
                        (or the device's specific IP) is the other half of
                        this -- do both, not just one.

    ZK_ADMS_DELAY_SECONDS
                        Default 10. Sent back to the device in the
                        handshake response as "Delay" -- how often
                        (seconds) it should call /iclock/getrequest when
                        the network is healthy. Smaller = snappier command
                        polling, more chatter; matters little for
                        attendance itself since Realtime=1 makes the
                        device push ATTLOG the instant it happens anyway,
                        regardless of this value.

    ZK_ADMS_ERROR_DELAY_SECONDS
                        Default 30. Sent back as "ErrorDelay" -- how long
                        the device waits before retrying us after a failed
                        connection attempt.
"""

import os
from typing import FrozenSet


def adms_allowed_serials() -> FrozenSet[str]:
    """Allowed device SNs, or an empty set meaning 'accept any'."""
    raw = os.getenv("ZK_ADMS_ALLOWED_SERIALS", "").strip()
    if not raw:
        return frozenset()
    return frozenset(s.strip() for s in raw.split(",") if s.strip())


def adms_delay_seconds() -> int:
    try:
        return max(2, int(os.getenv("ZK_ADMS_DELAY_SECONDS", "10")))
    except ValueError:
        return 10


def adms_error_delay_seconds() -> int:
    try:
        return max(5, int(os.getenv("ZK_ADMS_ERROR_DELAY_SECONDS", "30")))
    except ValueError:
        return 30
