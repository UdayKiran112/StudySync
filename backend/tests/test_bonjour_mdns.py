"""Unit checks for the Bonjour-backed mDNS registration (bonjour_mdns.py)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bonjour_mdns  # noqa: E402


class BonjourMdnsTests(unittest.TestCase):
    def test_register_empty_addrs_is_none(self):
        self.assertIsNone(bonjour_mdns.register_hostname_records(None, []))

    def test_load_client_does_not_raise(self):
        client = bonjour_mdns.load_bonjour_client()
        if client is None:
            self.skipTest("no Bonjour client (dnssd.dll) on this machine")
        for name in (
            "DNSServiceCreateConnection",
            "DNSServiceRegisterRecord",
            "DNSServiceProcessResult",
            "DNSServiceRefSockFD",
            "DNSServiceRefDeallocate",
        ):
            self.assertTrue(hasattr(client, name), name)

    def test_callback_kept_alive(self):
        self.assertIsNotNone(bonjour_mdns._reply_cb)

    def test_constants(self):
        self.assertEqual(bonjour_mdns._DNSServiceType_A, 1)
        self.assertEqual(bonjour_mdns._DNSServiceClass_IN, 1)
        self.assertEqual(bonjour_mdns._kDNSServiceErr_NoError, 0)
