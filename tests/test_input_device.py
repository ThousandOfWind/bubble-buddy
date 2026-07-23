import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from bubble_buddy import config
from bubble_buddy.cli import resolve_input_device


class FakeSd:
    """Minimal stand-in for the sounddevice module."""

    def __init__(self, devices, default=(-1, -1), reinit_devices=None):
        self._devices = devices
        self._reinit_devices = reinit_devices

        class _D:
            device = default

        self.default = _D()
        self.reinit_calls = 0

    def query_devices(self, index=None):
        if index is not None:
            return self._devices[index]
        return self._devices

    def _terminate(self):
        pass

    def _initialize(self):
        self.reinit_calls += 1
        if self._reinit_devices is not None:
            self._devices = self._reinit_devices


def _dev(name, inp):
    return {"name": name, "max_input_channels": inp}


class ResolveInputDeviceTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        )
        self._tmp.write("{}")
        self._tmp.close()
        self._prev = os.environ.get("BUBBLE_BUDDY_CONFIG")
        os.environ["BUBBLE_BUDDY_CONFIG"] = self._tmp.name
        config.load_config(reload=True)

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("BUBBLE_BUDDY_CONFIG", None)
        else:
            os.environ["BUBBLE_BUDDY_CONFIG"] = self._prev
        os.unlink(self._tmp.name)
        config.load_config(reload=True)

    def _set_config(self, data):
        with open(self._tmp.name, "w", encoding="utf-8") as f:
            import json

            json.dump(data, f)
        config.load_config(reload=True)

    def test_default_input_used_when_valid(self):
        sd = FakeSd([_dev("Speakers", 0), _dev("Real Mic", 2)], default=(1, 1))
        self.assertEqual(resolve_input_device(sd), (1, "Real Mic"))

    def test_skips_virtual_and_picks_real_mic(self):
        sd = FakeSd(
            [_dev("Microsoft Teams Audio", 2), _dev("USB Mic", 1)], default=(-1, -1)
        )
        self.assertEqual(resolve_input_device(sd), (1, "USB Mic"))

    def test_honors_configured_index(self):
        self._set_config({"input_device": "1"})
        sd = FakeSd([_dev("Mic A", 1), _dev("Mic B", 1)], default=(0, 0))
        self.assertEqual(resolve_input_device(sd), (1, "Mic B"))

    def test_honors_configured_name_substring(self):
        self._set_config({"input_device": "usb"})
        sd = FakeSd([_dev("Internal Mic", 1), _dev("USB Headset", 1)], default=(0, 0))
        self.assertEqual(resolve_input_device(sd), (1, "USB Headset"))

    def test_reinit_retry_finds_device(self):
        sd = FakeSd([], default=(-1, -1), reinit_devices=[_dev("Mic", 1)])
        self.assertEqual(resolve_input_device(sd), (0, "Mic"))
        self.assertEqual(sd.reinit_calls, 1)

    def test_raises_when_no_device_even_after_reinit(self):
        sd = FakeSd([_dev("Speakers", 0)], default=(-1, -1))
        with self.assertRaises(RuntimeError):
            resolve_input_device(sd)


if __name__ == "__main__":
    unittest.main()
