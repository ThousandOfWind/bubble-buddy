#!/usr/bin/env python3
"""Install the locked project through one explicitly approved package index.

No third-party bootstrap imports. This is an opt-in alternative to uv sync, not
an index/lockfile rewriter or an egress sandbox. See docs/approved-index.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
MIN_UV = (0, 12, 10)
BUILD_REQUIREMENTS = Path("packaging/build-requirements.txt")
# Preserve transport/trust and standard uv authentication, not resolver policy.
SAFE_UV_ENV = {
    "UV_SYSTEM_CERTS", "UV_NATIVE_TLS", "UV_OFFLINE", "UV_KEYRING_PROVIDER",
    "UV_CREDENTIALS_DIR",
}
PYTHON_INFO = (
    "import json,sys; print(json.dumps(dict(version=list(sys.version_info[:3]),"
    "platform=sys.platform,prefix=sys.prefix,base_prefix=sys.base_prefix)))"
)
NAME = r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?"
VERSION = (
    r"v?(?:[0-9]+!)?[0-9]+(?:\.[0-9]+)*"
    r"(?:[-_.]?(?:alpha|beta|preview|pre|rc|a|b|c)[-_.]?[0-9]*)?"
    r"(?:(?:-[0-9]+)|(?:[-_.]?(?:post|rev|r)[-_.]?[0-9]*))?"
    r"(?:[-_.]?dev[-_.]?[0-9]*)?(?:\+[a-z0-9]+(?:[-_.][a-z0-9]+)*)?"
)
PIN = re.compile(rf"{NAME}(?:\[{NAME}(?:,{NAME})*\])?=={VERSION}", re.I)
HASHED = re.compile(
    r"(?P<requirement>.+?)\s+--hash=sha256:[a-fA-F0-9]{64}"
    r"(?:\s+--hash=sha256:[a-fA-F0-9]{64})*"
)
MARKER_VARIABLES = {
    "python_version", "python_full_version", "os_name", "sys_platform",
    "platform_release", "platform_system", "platform_version", "platform_machine",
    "platform_python_implementation", "implementation_name", "implementation_version",
    "extra",
}
MARKER_TOKEN = re.compile(
    r'''\s*("[A-Za-z0-9._*+ ,!~<>=|()\-]*"|'[A-Za-z0-9._*+ ,!~<>=|()\-]*'|===|==|!=|<=|>=|~=|<|>|\(|\)|[a-z_]+)'''
)


class InstallError(Exception):
    """An actionable error whose text never includes subprocess output/URLs."""


def validate_index(value: str | None) -> str:
    """Accept a credential-free HTTPS URL, not a uv index name or local path."""
    error = "Supply one approved HTTPS index URL without credentials, query or fragment."
    if not value or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value):
        raise InstallError(error)
    if any(c in value for c in ("\\", "?", "#")):
        raise InstallError(error)
    try:
        url = urlsplit(value)
        port = url.port  # Raises for malformed/out-of-range ports.
        host = url.hostname
        if (url.scheme != "https" or not host or url.username is not None
                or url.password is not None or url.query or url.fragment
                or (port is not None and port == 0)):
            raise ValueError
        # Bracketed IPv6 is parsed by urlsplit; DNS names may be IDNA encoded.
        if ":" not in host:
            labels = host.rstrip(".").encode("idna").decode("ascii").split(".")
            if not all(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", x)
                       for x in labels):
                raise ValueError
        if "%" in url.netloc or url.netloc.endswith(":"):
            raise ValueError
    except (ValueError, UnicodeError):
        raise InstallError(error) from None
    return value


def child_environment(parent: dict[str, str]) -> dict[str, str]:
    """Do not inherit alternative indexes, configuration or installation targets.

    Standard HTTPS_PROXY/NO_PROXY/SSL_CERT_FILE etc. and normal uv auth storage
    remain available. No machine/user configuration file is modified.
    """
    env = {}
    for key, value in parent.items():
        upper = key.upper()
        if upper.startswith(("UV_", "PIP_")) and upper not in SAFE_UV_ENV:
            continue
        if upper in {"VIRTUAL_ENV", "CONDA_PREFIX", "PYTHONPATH", "PYTHONHOME",
                     "__PYVENV_LAUNCHER__"}:
            continue
        env[key] = value
    env.update(UV_NO_CONFIG="1", UV_PYTHON_DOWNLOADS="never", UV_NO_PROGRESS="1",
               PIP_CONFIG_FILE=os.devnull)
    return env


def validate_marker(marker: str) -> None:
    """Recognize the exported PEP 508 marker grammar without evaluating it.

    Unknown syntax fails closed; uv still checks marker semantics on install.
    No host-side marker filtering: the chosen interpreter selects its branches.
    """
    tokens = []
    position = 0
    marker = marker.strip()
    while position < len(marker):
        match = MARKER_TOKEN.match(marker, position)
        if not match:
            raise ValueError("unsupported marker")
        tokens.append(match[1])
        position = match.end()
    if not tokens or len(tokens) > 256:
        raise ValueError("empty or excessive marker")
    cursor = 0

    def operand() -> None:
        nonlocal cursor
        if cursor >= len(tokens):
            raise ValueError("missing operand")
        token = tokens[cursor]
        if token not in MARKER_VARIABLES and not token.startswith(("'", '"')):
            raise ValueError("unknown operand")
        cursor += 1

    def atom() -> None:
        nonlocal cursor
        if cursor < len(tokens) and tokens[cursor] == "(":
            cursor += 1
            expression()
            if cursor >= len(tokens) or tokens[cursor] != ")":
                raise ValueError("missing parenthesis")
            cursor += 1
            return
        operand()
        if cursor >= len(tokens):
            raise ValueError("missing comparison")
        op = tokens[cursor]
        cursor += 1
        if op == "not":
            if cursor >= len(tokens) or tokens[cursor] != "in":
                raise ValueError("missing in")
            cursor += 1
        elif op not in {"===", "==", "!=", "<=", ">=", "~=", "<", ">", "in"}:
            raise ValueError("unknown comparison")
        operand()

    def conjunction() -> None:
        nonlocal cursor
        atom()
        while cursor < len(tokens) and tokens[cursor] == "and":
            cursor += 1
            atom()

    def expression() -> None:
        nonlocal cursor
        conjunction()
        while cursor < len(tokens) and tokens[cursor] == "or":
            cursor += 1
            conjunction()

    expression()
    if cursor != len(tokens):
        raise ValueError("trailing marker content")


def validate_requirements(text: str) -> str:
    """Validate, but return the ORIGINAL pins, markers, hashes and formatting.

    Only registry pins and SHA-256 hashes are accepted. In particular, do not
    rewrite direct URL requirements into registry pins or drop their lines.
    """
    if any(ord(c) < 32 and c not in "\r\n\t" for c in text) or "\x7f" in text:
        raise InstallError("Unsupported control character in requirements.")
    pending = ""
    count = 0
    for number, physical in enumerate(text.splitlines(), 1):
        line = physical.strip()
        if not line or line.startswith("#"):
            if pending:
                raise InstallError(f"Broken requirement continuation near line {number}.")
            continue
        continued = line.endswith("\\")
        pending += (line[:-1] if continued else line) + " "
        if continued:
            continue
        logical = pending.strip()
        pending = ""
        match = HASHED.fullmatch(logical)
        if not match:
            raise InstallError(f"Expected an exact pin with SHA-256 hashes near line {number}.")
        pin, separator, marker = match["requirement"].partition(";")
        if not PIN.fullmatch(pin.strip()):
            raise InstallError(f"Non-registry or malformed dependency near line {number}.")
        if separator:
            try:
                validate_marker(marker)
            except ValueError:
                raise InstallError(f"Unsupported dependency marker near line {number}.") from None
        count += 1
    if pending or not count:
        raise InstallError("Requirements are empty or have an unfinished continuation.")
    return text


def venv_python(directory: Path, windows: bool | None = None) -> Path:
    if windows is None:
        windows = os.name == "nt"
    return directory / ("Scripts/python.exe" if windows else "bin/python")


def target_environment(root: Path, value: str, sync_existing: bool) -> tuple[Path, bool]:
    target = Path(os.path.abspath(root / value))
    if (target == root or not target.is_relative_to(root) or target.is_symlink()
            or target.resolve() != target or ".git" in target.relative_to(root).parts):
        raise InstallError("The virtualenv must be a non-redirected directory inside this checkout.")
    exists = target.exists()
    if exists and not sync_existing:
        raise InstallError("Virtualenv already exists; use --sync-existing to update its locked packages, "
                           "or choose a new --venv path. Nothing was removed.")
    if exists and (not (target / "pyvenv.cfg").is_file() or not venv_python(target).is_file()):
        raise InstallError("Existing target is not a virtualenv; it will not be recreated.")
    if sync_existing and not exists:
        raise InstallError("--sync-existing requires an existing virtualenv.")
    return target, exists


class Runner:
    def __init__(self, root: Path, env: dict[str, str]):
        self.root = root
        self.env = env

    def run(self, args: list[str], stage: str, *, offline: bool = False) -> str:
        env = self.env.copy()
        if offline:
            env.update(UV_OFFLINE="1", PIP_NO_INDEX="1")
        try:
            result = subprocess.run(args, cwd=self.root, env=env, shell=False,
                                    capture_output=True, text=True, encoding="utf-8",
                                    errors="replace", check=False)
        except OSError:
            raise InstallError(f"Could not start {stage}; check the installed prerequisite.") from None
        if result.returncode:
            # uv may print the index, signed redirect URLs, or proxy credentials.
            # Deliberately do not forward its stdout/stderr or CalledProcessError.
            raise InstallError(f"{stage} failed (exit {result.returncode}). Installer output withheld "
                               "to protect feed/auth details. Check lock freshness, approved-feed "
                               "access and availability of the pinned, hash-matching wheels.")
        return result.stdout


def python_info(runner: Runner, python: Path, expected_env: Path | None = None) -> dict:
    if not python.is_file():
        raise InstallError("Python is missing; supply an already installed interpreter path.")
    output = runner.run([str(python), "-I", "-c", PYTHON_INFO], "Python preflight", offline=True)
    try:
        info = json.loads(output)
        if (tuple(info["version"]) < (3, 10) or info["platform"] not in {"win32", "darwin"}):
            raise ValueError
        if expected_env is not None:
            if (Path(info["prefix"]).resolve() != expected_env
                    or info["prefix"] == info["base_prefix"]):
                raise ValueError
    except (ValueError, TypeError, KeyError):
        raise InstallError("Expected Python >=3.10 on Windows/macOS in the selected environment.") from None
    return info


def install(args: argparse.Namespace, *, root: Path = ROOT,
            parent_env: dict[str, str] | None = None, runner_factory=Runner) -> None:
    root = root.resolve()
    parent = dict(os.environ if parent_env is None else parent_env)
    index = validate_index(args.index_url if args.index_url is not None
                           else parent.get("UV_DEFAULT_INDEX"))
    env = child_environment(parent)
    target, existing = target_environment(root, args.venv, args.sync_existing)
    python = venv_python(target) if existing else Path(args.python).expanduser().resolve()
    tracked = [root / "pyproject.toml", root / "uv.lock"]
    if not args.dependencies_only:
        tracked.append(root / BUILD_REQUIREMENTS)
    try:
        original = {path: path.read_bytes() for path in tracked}
    except OSError:
        raise InstallError("Required project manifest, lock or build requirements are missing.") from None

    def unchanged() -> None:
        if any(not path.is_file() or path.read_bytes() != data for path, data in original.items()):
            raise InstallError("Project inputs changed during installation; stopped without relocking. "
                               "Review the environment before use.")

    with tempfile.TemporaryDirectory(prefix="bubble-buddy-install-") as temporary:
        work = Path(temporary)
        cache = Path(args.cache_dir).expanduser().resolve() if args.cache_dir else work / "cache"
        runner = runner_factory(root, env)
        common = ["--no-config", "--no-python-downloads", "--cache-dir", str(cache)]

        def uv(arguments: list[str], stage: str, *, offline: bool = False) -> str:
            command = ["uv", *arguments, *common]
            if offline:
                command.append("--offline")
            print(f"{stage}...", flush=True)
            try:
                return runner.run(command, stage, offline=offline)
            finally:
                unchanged()

        try:
            info = python_info(runner, python, target if existing else None)
            version_text = runner.run(["uv", "--version"], "uv preflight", offline=True)
            unchanged()
            version = re.match(r"uv (\d+)\.(\d+)\.(\d+)(?:\s|$)", version_text)
            if not version or tuple(map(int, version.groups())) < MIN_UV:
                raise InstallError("This helper requires an already installed uv >=0.12.10.")
            export_args = ["export", "--python", str(python), "--no-emit-project",
                           "--format", "requirements.txt", "--no-header", "--no-annotate",
                           "--no-default-groups"]
            if args.dev:
                export_args += ["--group", "dev"]
            # --frozen alone does not detect an out-of-date lock. Neither call
            # receives the replacement index, and both are network-disabled.
            uv([*export_args, "--locked"], "Offline lock check", offline=True)
            exported = validate_requirements(
                uv([*export_args, "--frozen"], "Frozen dependency export", offline=True))
            requirements = work / "dependencies.txt"
            requirements.write_bytes(exported.encode("utf-8"))
            build_requirements = work / "build-requirements.txt"
            if not args.dependencies_only:
                build_requirements.write_bytes(validate_requirements(
                    original[root / BUILD_REQUIREMENTS].decode("utf-8")).encode("utf-8"))

            if not existing:
                # Reserve exclusively: never let uv replace a pre-existing env.
                target.mkdir(parents=True, exist_ok=False)
                uv(["venv", "--allow-existing", "--python", str(python), str(target)],
                   "Virtualenv creation", offline=True)
            target_python = venv_python(target)
            python_info(runner, target_python, target)
            unchanged()
            (target / "bubble-buddy-install.json").write_text(
                '{"schema_version": 1, "status": "incomplete"}\n', encoding="utf-8")
            registry_args = ["pip", "install", "--default-index", index, "--no-deps",
                             "--require-hashes", "--only-binary", ":all:", "--no-sources"]
            dependency_args = [*registry_args, "--python", str(target_python), "-r", str(requirements)]
            if existing:
                # Reapply hashes even for previously satisfied packages. Only
                # the exported closure is reinstalled; no extraneous pruning.
                dependency_args.append("--reinstall")
            uv(dependency_args, "Locked dependency installation")
            wheel_hash = None
            if not args.dependencies_only:
                build_env = work / "build-env"
                uv(["venv", "--python", str(target_python), str(build_env)],
                   "Build virtualenv creation", offline=True)
                build_python = venv_python(build_env)
                uv([*registry_args, "--python", str(build_python), "-r", str(build_requirements)],
                   "Pinned build-backend installation")
                wheel_dir = work / "wheel"
                uv(["build", str(root), "--python", str(build_python), "--wheel",
                    "--out-dir", str(wheel_dir), "--force-pep517", "--no-build-isolation",
                    "--no-index", "--no-sources"], "Local project wheel build", offline=True)
                wheels = list(wheel_dir.glob("*.whl"))
                if (len(wheels) != 1 or wheels[0].is_symlink()
                        or not wheels[0].name.startswith("bubble_buddy-")):
                    raise InstallError("Local build did not produce exactly one Bubble Buddy wheel.")
                wheel = wheels[0].resolve()
                wheel_hash = hashlib.sha256(wheel.read_bytes()).hexdigest()
                local_req = work / "local-project.txt"
                local_req.write_text(f"{wheel.as_uri()} --hash=sha256:{wheel_hash}\n", encoding="utf-8")
                # This owned, newly built and hashed wheel is the sole direct-file
                # exception. Runtime dependencies cannot be resolved in this step.
                uv(["pip", "install", "--python", str(target_python), "--no-index", "--no-deps",
                    "--require-hashes", "--reinstall-package", "bubble-buddy", "-r", str(local_req)],
                   "Local project installation", offline=True)
            uv(["pip", "check", "--python", str(target_python)], "Dependency consistency check", offline=True)
            unchanged()
            receipt = {
                "schema_version": 1, "status": "complete", "python_version": info["version"], "dev": args.dev,
                "project_installed": not args.dependencies_only,
                "pyproject_sha256": hashlib.sha256(original[root / "pyproject.toml"]).hexdigest(),
                "lock_sha256": hashlib.sha256(original[root / "uv.lock"]).hexdigest(),
                "requirements_sha256": hashlib.sha256(exported.encode("utf-8")).hexdigest(),
                "project_wheel_sha256": wheel_hash,
                "build_requirements_sha256": (hashlib.sha256(
                    original[root / BUILD_REQUIREMENTS]).hexdigest() if not args.dependencies_only else None),
            }
            (target / "bubble-buddy-install.json").write_text(
                json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        finally:
            unchanged()
    if args.dependencies_only:
        print("Locked dependencies installed; local project NOT installed (--dependencies-only).")
    else:
        print("Locked dependencies and local project installed. Use the venv directly or uv run --no-sync.")


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's normal error can echo a mistyped credential-bearing URL.
        raise InstallError("Invalid command arguments; use --help for supported options.")


def parser() -> argparse.ArgumentParser:
    result = SafeArgumentParser(description=__doc__)
    result.add_argument("--index-url", help="Explicit approved HTTPS simple index (or UV_DEFAULT_INDEX)")
    result.add_argument("--python", default=sys.executable, help="Installed Python path for a NEW venv")
    result.add_argument("--venv", default=".venv", help="Virtualenv path inside the checkout (default: .venv)")
    result.add_argument("--sync-existing", action="store_true",
                        help="Reinstall only the locked closure in an existing venv; never prune/recreate")
    result.add_argument("--dev", action="store_true", help="Include the locked dev group (PyInstaller)")
    result.add_argument("--dependencies-only", action="store_true", help="Skip local project and build backend")
    result.add_argument("--cache-dir", help="Explicit uv cache; default is a new temporary cache")
    return result


def main(argv=None) -> int:
    try:
        install(parser().parse_args(argv))
    except InstallError as exc:
        print(f"Installation stopped: {exc}", file=sys.stderr)
        return 1
    except OSError:
        print("Installation stopped by a filesystem error; existing environments were not removed.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
