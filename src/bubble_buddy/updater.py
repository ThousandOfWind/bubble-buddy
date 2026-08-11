from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


RELEASE_API_URL = "https://api.github.com/repos/ThousandOfWind/bubble-buddy/releases/latest"
TRUSTED_DOWNLOAD_PREFIX = (
    "https://github.com/ThousandOfWind/bubble-buddy/releases/download/"
)
CHECKSUM_ASSET_NAME = "SHA256SUMS.txt"
MAX_INSTALLER_BYTES = 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024


class UpdateError(RuntimeError):
    pass


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    tag_name: str
    asset_name: str
    asset_url: str
    checksum_url: str
    asset_size: int = 0


def current_app_version() -> str:
    build_info = packaged_build_info()
    if build_info:
        version_value = str(build_info.get("version") or "")
        _version_tuple(version_value)
        return version_value
    try:
        from importlib.metadata import version

        return version("bubble-buddy")
    except Exception:
        return "0.1.1"


def packaged_build_info() -> dict[str, Any]:
    root = getattr(sys, "_MEIPASS", "")
    if not isinstance(root, str) or not root:
        return {}
    try:
        payload = json.loads(
            (Path(root) / "bubble-buddy-build.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _version_tuple(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", value.strip())
    if not match:
        raise UpdateError(f"Unsupported release version: {value!r}")
    return tuple(int(part) for part in match.groups())


def is_newer_version(candidate: str, current: str) -> bool:
    return _version_tuple(candidate) > _version_tuple(current)


def detect_packaged_edition(
    system: str | None = None,
    module_present: Callable[[str], bool] | None = None,
    build_info: dict[str, Any] | None = None,
) -> str:
    """Infer the installed edition from modules actually bundled in the app."""
    metadata = packaged_build_info() if build_info is None else build_info
    declared = str(metadata.get("edition") or "").lower()
    if declared in {"azure", "full"}:
        return declared
    platform_name = system or sys.platform
    present = module_present or (lambda name: importlib.util.find_spec(name) is not None)
    if platform_name == "win32":
        return "full" if present("faster_whisper") else "azure"
    if platform_name == "darwin":
        return "full" if present("mlx_whisper") else "azure"
    raise UpdateError(f"Automatic updates are unsupported on {platform_name}.")


def expected_asset_name(version: str, system: str, edition: str) -> str:
    if edition not in {"azure", "full"}:
        raise UpdateError(f"Unknown app edition: {edition!r}")
    suffix = "-Full" if edition == "full" else ""
    if system == "win32":
        return f"BubbleBuddy{suffix}-Setup-{version}.exe"
    if system == "darwin":
        return f"BubbleBuddy{suffix}-{version}.dmg"
    raise UpdateError(f"Automatic updates are unsupported on {system}.")


def _request_json(
    url: str,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "Bubble-Buddy-Updater",
        },
    )
    with opener(request, timeout=20) as response:
        raw = response.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise UpdateError("GitHub release response is unexpectedly large.")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError("GitHub returned an invalid release response.") from exc
    if not isinstance(payload, dict):
        raise UpdateError("GitHub returned an invalid release response.")
    return payload


def check_for_update(
    current_version: str,
    *,
    frozen: bool | None = None,
    system: str | None = None,
    opener: Callable[..., Any] = urllib.request.urlopen,
    module_present: Callable[[str], bool] | None = None,
) -> UpdateInfo | None:
    """Return a verified-release plan, or None for source/current installations."""
    if frozen is None:
        frozen = bool(getattr(sys, "frozen", False))
    if not frozen:
        return None

    platform_name = system or sys.platform
    edition = detect_packaged_edition(platform_name, module_present)
    release = _request_json(RELEASE_API_URL, opener)
    tag_name = str(release.get("tag_name") or "")
    version = tag_name.removeprefix("v")
    if not is_newer_version(version, current_version):
        return None

    assets = release.get("assets")
    if not isinstance(assets, list):
        raise UpdateError("Latest GitHub release has no downloadable assets.")
    by_name = {
        str(asset.get("name")): asset
        for asset in assets
        if isinstance(asset, dict) and asset.get("name")
    }
    asset_name = expected_asset_name(version, platform_name, edition)
    installer = by_name.get(asset_name)
    manifest = by_name.get(CHECKSUM_ASSET_NAME)
    if installer is None:
        raise UpdateError(f"Latest release is missing {asset_name}.")
    if manifest is None:
        raise UpdateError("Latest release has no SHA256SUMS.txt; refusing an unsafe update.")

    asset_url = str(installer.get("browser_download_url") or "")
    checksum_url = str(manifest.get("browser_download_url") or "")
    if not asset_url.startswith(TRUSTED_DOWNLOAD_PREFIX) or not checksum_url.startswith(
        TRUSTED_DOWNLOAD_PREFIX
    ):
        raise UpdateError("Release contains an untrusted download URL.")
    size = int(installer.get("size") or 0)
    if size < 0 or size > MAX_INSTALLER_BYTES:
        raise UpdateError("Release installer is unexpectedly large.")
    return UpdateInfo(
        version=version,
        tag_name=tag_name,
        asset_name=asset_name,
        asset_url=asset_url,
        checksum_url=checksum_url,
        asset_size=size,
    )


def parse_checksums(content: str) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for line in content.splitlines():
        match = re.fullmatch(r"([0-9a-fA-F]{64})\s{2}([^\r\n/\\]+)", line.strip())
        if match:
            checksums[match.group(2)] = match.group(1).lower()
    return checksums


def _read_small_asset(
    url: str,
    opener: Callable[..., Any],
) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Bubble-Buddy-Updater"},
    )
    with opener(request, timeout=30) as response:
        content = response.read(MAX_MANIFEST_BYTES + 1)
    if len(content) > MAX_MANIFEST_BYTES:
        raise UpdateError("Checksum manifest is unexpectedly large.")
    return content


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_update(
    update: UpdateInfo,
    *,
    root: Path | None = None,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> Path:
    """Download to the user data directory and verify the published SHA-256."""
    manifest_bytes = _read_small_asset(update.checksum_url, opener)
    try:
        checksums = parse_checksums(manifest_bytes.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise UpdateError("Checksum manifest is not UTF-8.") from exc
    expected = checksums.get(update.asset_name)
    if expected is None:
        raise UpdateError(f"Checksum manifest has no entry for {update.asset_name}.")

    update_root = root or (Path.home() / ".bubble-buddy" / "updates")
    destination_dir = update_root / update.tag_name
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / update.asset_name
    if destination.is_file() and _sha256(destination) == expected:
        return destination

    partial = destination.with_suffix(destination.suffix + ".part")
    request = urllib.request.Request(
        update.asset_url,
        headers={"User-Agent": "Bubble-Buddy-Updater"},
    )
    digest = hashlib.sha256()
    total = 0
    try:
        with opener(request, timeout=60) as response, partial.open("wb") as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_INSTALLER_BYTES:
                    raise UpdateError("Downloaded installer exceeds the safety limit.")
                digest.update(chunk)
                handle.write(chunk)
        if update.asset_size and total != update.asset_size:
            raise UpdateError(
                f"Installer size mismatch: expected {update.asset_size}, got {total}."
            )
        if digest.hexdigest() != expected:
            raise UpdateError("Installer SHA-256 verification failed.")
        os.replace(partial, destination)
        return destination
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def launch_update_installer(path: Path, system: str | None = None) -> None:
    """Launch a verified installer after the UI has obtained user confirmation."""
    platform_name = system or sys.platform
    if platform_name == "win32":
        if path.suffix.lower() != ".exe":
            raise UpdateError("Expected a Windows .exe installer.")
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen(
            [
                str(path),
                "/SILENT",
                "/SUPPRESSMSGBOXES",
                "/NORESTART",
                "/CLOSEAPPLICATIONS",
            ],
            close_fds=True,
            creationflags=flags,
        )
        return
    if platform_name == "darwin":
        if path.suffix.lower() != ".dmg":
            raise UpdateError("Expected a macOS .dmg installer.")
        subprocess.Popen(["open", str(path)], close_fds=True)
        return
    raise UpdateError(f"Automatic updates are unsupported on {platform_name}.")
