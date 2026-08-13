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
    _ENV_KEYS = (
        "ZK_ATTENDANCE_MODE",
        "ZK_DEVICE_IP",
        "ZK_BUFFER_CLEAR_PERCENT",
        "ZK_BUFFER_ALERT_PERCENT",
        "ZK_BUFFER_AUTO_CLEAR",
        "ZK_BUFFER_ARCHIVE_DIR",
        "STUDYSYNC_DB_PATH",
    )

    def tearDown(self):
        for key in self._ENV_KEYS:
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

    # --- buffer management settings ----------------------------------------

    def test_buffer_clear_percent_defaults_to_95_and_clamps(self):
        os.environ.pop("ZK_BUFFER_CLEAR_PERCENT", None)
        self.assertEqual(config.buffer_clear_percent(), 95)
        with self._patch_env(ZK_BUFFER_CLEAR_PERCENT="50"):
            self.assertEqual(config.buffer_clear_percent(), 50)
        with self._patch_env(ZK_BUFFER_CLEAR_PERCENT="garbage"):
            self.assertEqual(config.buffer_clear_percent(), 95)
        with self._patch_env(ZK_BUFFER_CLEAR_PERCENT="150"):
            self.assertEqual(config.buffer_clear_percent(), 100)
        with self._patch_env(ZK_BUFFER_CLEAR_PERCENT="0"):
            self.assertEqual(config.buffer_clear_percent(), 1)

    def test_buffer_alert_percent_defaults_to_80(self):
        os.environ.pop("ZK_BUFFER_ALERT_PERCENT", None)
        self.assertEqual(config.buffer_alert_percent(), 80)
        with self._patch_env(ZK_BUFFER_ALERT_PERCENT="garbage"):
            self.assertEqual(config.buffer_alert_percent(), 80)

    def test_buffer_auto_clear_defaults_on_and_parses_falsy(self):
        os.environ.pop("ZK_BUFFER_AUTO_CLEAR", None)
        self.assertTrue(config.buffer_auto_clear_enabled())
        for value in ("0", "false", "no", "off", "FALSE"):
            with self._patch_env(ZK_BUFFER_AUTO_CLEAR=value):
                self.assertFalse(config.buffer_auto_clear_enabled())
        for value in ("1", "true", "yes", "on"):
            with self._patch_env(ZK_BUFFER_AUTO_CLEAR=value):
                self.assertTrue(config.buffer_auto_clear_enabled())

    def test_buffer_archive_dir_respects_env_and_db_default(self):
        with self._patch_env(
            ZK_BUFFER_ARCHIVE_DIR="C:\\archives", STUDYSYNC_DB_PATH=""
        ):
            self.assertEqual(config.buffer_archive_dir(), "C:\\archives")
        with self._patch_env(
            ZK_BUFFER_ARCHIVE_DIR="", STUDYSYNC_DB_PATH="C:\\data\\library.db"
        ):
            self.assertEqual(
                config.buffer_archive_dir(), "C:\\data\\device_punches"
            )
        with self._patch_env(ZK_BUFFER_ARCHIVE_DIR="", STUDYSYNC_DB_PATH=""):
            self.assertTrue(config.buffer_archive_dir().endswith("device_punches"))


if __name__ == "__main__":
    unittest.main()
