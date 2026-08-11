"""Unit tests for the pyzk integration settings used by the venue rollout.

Exercises zkteco/config.py's attendance-mode / device-config switches
against a patched environment (no hardware). Run from the project root:
    & .\\study_sync\\Scripts\\python.exe -m unittest discover -s backend/tests -v
"""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from zkteco import config  # noqa: E402


class ZktecoConfigTests(unittest.TestCase):
    def tearDown(self):
        for key in ("ZK_ATTENDANCE_MODE", "ZK_DEVICE_IP"):
            os.environ.pop(key, None)

    def _patch_env(self, **values):
        return mock.patch.dict(os.environ, values, clear=False)

    # --- attendance_mode ---------------------------------------------------

    def test_attendance_mode_defaults_to_poll(self):
        os.environ.pop("ZK_ATTENDANCE_MODE", None)
        self.assertEqual(config.attendance_mode(), "poll")

    def test_attendance_mode_accepts_live_and_both(self):
        with self._patch_env(ZK_ATTENDANCE_MODE="live"):
            self.assertEqual(config.attendance_mode(), "live")
        with self._patch_env(ZK_ATTENDANCE_MODE="both"):
            self.assertEqual(config.attendance_mode(), "both")

    def test_attendance_mode_falls_back_to_poll_on_garbage(self):
        with self._patch_env(ZK_ATTENDANCE_MODE="sideways"):
            self.assertEqual(config.attendance_mode(), "poll")

    # --- device_config -----------------------------------------------------

    def test_device_config_none_without_ip(self):
        os.environ.pop("ZK_DEVICE_IP", None)
        self.assertIsNone(config.device_config())

    def test_device_config_parses_defaults(self):
        with self._patch_env(ZK_DEVICE_IP="192.168.1.201"):
            cfg = config.device_config()
            self.assertEqual(cfg.ip, "192.168.1.201")
            self.assertEqual(cfg.port, 4370)
            self.assertEqual(cfg.comm_key, 0)


if __name__ == "__main__":
    unittest.main()
