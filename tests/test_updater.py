from __future__ import annotations

import hashlib
import io
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from bubble_buddy import updater


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class _Opener:
    def __init__(self, responses: dict[str, bytes]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def __call__(self, request, timeout):
        url = request.full_url
        self.calls.append(url)
        return _Response(self.responses[url])


def _release(version: str, asset_name: str, size: int = 0) -> bytes:
    prefix = (
        f"https://github.com/ThousandOfWind/bubble-buddy/releases/download/v{version}/"
    )
    return json.dumps(
        {
            "tag_name": f"v{version}",
            "assets": [
                {
                    "name": asset_name,
                    "browser_download_url": prefix + asset_name,
                    "size": size,
                },
                {
                    "name": updater.CHECKSUM_ASSET_NAME,
                    "browser_download_url": prefix + updater.CHECKSUM_ASSET_NAME,
                },
            ],
        }
    ).encode()


class VersionTest(unittest.TestCase):
    def test_numeric_version_comparison(self):
        self.assertTrue(updater.is_newer_version("0.10.0", "0.9.9"))
        self.assertFalse(updater.is_newer_version("v1.0.0", "1.0.0"))

    def test_rejects_non_release_versions(self):
        with self.assertRaises(updater.UpdateError):
            updater.is_newer_version("1.2", "1.1.0")


class EditionAndAssetTest(unittest.TestCase):
    def test_asset_names_cover_platform_and_edition(self):
        self.assertEqual(
            updater.expected_asset_name("1.2.3", "win32", "azure"),
            "BubbleBuddy-Setup-1.2.3.exe",
        )
        self.assertEqual(
            updater.expected_asset_name("1.2.3", "win32", "full"),
            "BubbleBuddy-Full-Setup-1.2.3.exe",
        )
        self.assertEqual(
            updater.expected_asset_name("1.2.3", "darwin", "azure"),
            "BubbleBuddy-1.2.3.dmg",
        )
        self.assertEqual(
            updater.expected_asset_name("1.2.3", "darwin", "full"),
            "BubbleBuddy-Full-1.2.3.dmg",
        )

    def test_build_metadata_wins_over_module_inference(self):
        edition = updater.detect_packaged_edition(
            "win32",
            module_present=lambda _name: True,
            build_info={"edition": "azure"},
        )
        self.assertEqual(edition, "azure")

    def test_old_build_infers_full_edition_from_bundled_module(self):
        edition = updater.detect_packaged_edition(
            "darwin",
            module_present=lambda name: name == "mlx_whisper",
            build_info={},
        )
        self.assertEqual(edition, "full")


class UpdateCheckTest(unittest.TestCase):
    def test_source_checkout_never_calls_github(self):
        opener = mock.Mock(side_effect=AssertionError("network should not be used"))
        self.assertIsNone(
            updater.check_for_update("0.1.0", frozen=False, opener=opener)
        )
        opener.assert_not_called()

    def test_selects_exact_matching_release_asset(self):
        asset = updater.expected_asset_name("0.2.0", "win32", "full")
        opener = _Opener(
            {updater.RELEASE_API_URL: _release("0.2.0", asset, size=123)}
        )
        update = updater.check_for_update(
            "0.1.0",
            frozen=True,
            system="win32",
            opener=opener,
            module_present=lambda name: name == "faster_whisper",
        )
        self.assertIsNotNone(update)
        assert update is not None
        self.assertEqual(update.asset_name, asset)
        self.assertEqual(update.asset_size, 123)

    def test_refuses_release_without_checksum_manifest(self):
        asset = updater.expected_asset_name("0.2.0", "win32", "azure")
        payload = json.loads(_release("0.2.0", asset))
        payload["assets"] = payload["assets"][:1]
        opener = _Opener(
            {updater.RELEASE_API_URL: json.dumps(payload).encode()}
        )
        with self.assertRaisesRegex(updater.UpdateError, "SHA256SUMS"):
            updater.check_for_update(
                "0.1.0",
                frozen=True,
                system="win32",
                opener=opener,
                module_present=lambda _name: False,
            )


class DownloadTest(unittest.TestCase):
    def _update(self, data: bytes) -> tuple[updater.UpdateInfo, _Opener]:
        asset_name = "BubbleBuddy-Setup-0.2.0.exe"
        base = (
            "https://github.com/ThousandOfWind/bubble-buddy/releases/download/"
            "v0.2.0/"
        )
        digest = hashlib.sha256(data).hexdigest()
        info = updater.UpdateInfo(
            version="0.2.0",
            tag_name="v0.2.0",
            asset_name=asset_name,
            asset_url=base + asset_name,
            checksum_url=base + updater.CHECKSUM_ASSET_NAME,
            asset_size=len(data),
        )
        opener = _Opener(
            {
                info.asset_url: data,
                info.checksum_url: f"{digest}  {asset_name}\n".encode(),
            }
        )
        return info, opener

    def test_downloads_verifies_and_atomically_renames(self):
        info, opener = self._update(b"verified installer")
        with TemporaryDirectory() as temp_dir:
            path = updater.download_update(
                info, root=Path(temp_dir), opener=opener
            )
            self.assertEqual(path.read_bytes(), b"verified installer")
            self.assertFalse(path.with_suffix(".exe.part").exists())

    def test_checksum_failure_removes_partial_download(self):
        info, opener = self._update(b"tampered installer")
        opener.responses[info.checksum_url] = (
            f"{'0' * 64}  {info.asset_name}\n".encode()
        )
        with TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(updater.UpdateError, "SHA-256"):
                updater.download_update(
                    info, root=Path(temp_dir), opener=opener
                )
            self.assertFalse(any(Path(temp_dir).rglob("*.part")))


class BuildInfoTest(unittest.TestCase):
    def test_reads_frozen_build_version(self):
        with TemporaryDirectory() as temp_dir:
            Path(temp_dir, "bubble-buddy-build.json").write_text(
                '{"version": "2.3.4", "edition": "full"}',
                encoding="utf-8",
            )
            with mock.patch.object(sys, "_MEIPASS", temp_dir, create=True):
                self.assertEqual(updater.current_app_version(), "2.3.4")


class UpdateConfigTest(unittest.TestCase):
    def test_auto_update_is_opt_in(self):
        from bubble_buddy import config

        self.assertIs(config.DEFAULTS["auto_update"], False)

    def test_nested_app_flag_is_loaded(self):
        from bubble_buddy import config

        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir, "config.json")
            path.write_text('{"app": {"auto_update": true}}', encoding="utf-8")
            with mock.patch.dict(
                "os.environ", {"BUBBLE_BUDDY_CONFIG": str(path)}, clear=False
            ):
                loaded = config.load_config(reload=True)
                self.assertIs(loaded["auto_update"], True)
        config.load_config(reload=True)


if __name__ == "__main__":
    unittest.main()
