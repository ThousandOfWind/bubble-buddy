"""Offline bootstrap tests: no package downloads, app imports or real subprocesses."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("install_from_index", ROOT / "tools/install_from_index.py")
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)
HASH_A = "a" * 64
HASH_B = "b" * 64
INDEX = "https://packages.example.invalid/simple"
EXPORT = (
    "# An offline exported fixture\r\n"
    "numpy==2.2.6 ; python_full_version < '3.11' \\\r\n"
    f"    --hash=sha256:{HASH_A} \\\r\n    --hash=sha256:{HASH_B}\r\n"
    f"numpy==2.4.6 ; python_full_version >= '3.11' --hash=sha256:{HASH_B}\r\n"
    f"uiautomation==2.0.20 ; sys_platform == 'win32' --hash=sha256:{HASH_A}\r\n"
    f"mlx-whisper==0.4.3 ; sys_platform == 'darwin' --hash=sha256:{HASH_B}\r\n"
)


class RequirementsTest(unittest.TestCase):
    def test_preserves_exact_text_hashes_markers_and_line_endings(self):
        self.assertEqual(helper.validate_requirements(EXPORT), EXPORT)

    def test_accepts_exported_boolean_markers_and_exact_versions(self):
        markers = [
            "(python_full_version >= '3.12' and sys_platform != 'win32') or os_name == 'nt'",
            "python_full_version == '3.11.*'", "platform_machine not in 'x86_64, AMD64'",
            "'darwin' == sys_platform", 'implementation_name == "cpython"',
        ]
        for version in ["1.0", "1!2.3rc1", "2.0.post1", "3.1.dev2", "1.0+cpu", "v1.2"]:
            for marker in markers:
                text = f"some-package[extra]=={version} ; {marker} --hash=sha256:{HASH_A}\n"
                with self.subTest(version=version, marker=marker):
                    self.assertEqual(helper.validate_requirements(text), text)

    def test_direct_urls_options_and_unpinned_requirements_fail_closed(self):
        invalid = [
            "pkg>=1", "pkg==1.*", "pkg===1", "pkg==banana", "pkg", "-e .", ".",
            "pkg @ https://user:secret@example.invalid/pkg.whl", "https://example.invalid/pkg.whl",
            "git+https://example.invalid/pkg", "pkg @ file:///tmp/pkg.whl", "./pkg.whl",
            "C:\\wheel\\pkg.whl", "--index-url https://example.invalid/simple", "--extra-index-url X",
            "--find-links .", "-r nested.txt", "-c constraints.txt", "--trusted-host example.invalid",
            "pkg==1 --no-deps", "pkg==1 # ignored", "pkg==1;", "pkg==1; unknown == 'yes'",
            "pkg==1; python_version", "pkg==1; python_version == '3.10' garbage",
            "pkg==1; (python_version == '3.10'", "pkg==1; python_version == '3.10' or",
            "pkg==1; python_version not '3.10'", "pkg==1; python_version == '3.10'); evil()",
        ]
        for requirement in invalid:
            with self.subTest(requirement=requirement):
                with self.assertRaises(helper.InstallError) as error:
                    helper.validate_requirements(f"{requirement} --hash=sha256:{HASH_A}\n")
                self.assertNotIn("secret", str(error.exception))
                self.assertNotIn("example.invalid", str(error.exception))

    def test_missing_bad_hashes_and_broken_continuations(self):
        cases = ["", "# only a comment\n", "pkg==1", f"pkg==1 --hash=md5:{HASH_A}",
                 "pkg==1 --hash=sha256:abc", f"pkg==1 --hash=sha256:{'z' * 64}",
                 f"pkg==1 --hash=sha256:{HASH_A} --extra-index-url https://bad.invalid",
                 "pkg==1 \\\n", "pkg==1 \\\n# interrupt\n", "pkg==1\x00", "pkg==1\x1b"]
        for text in cases:
            with self.subTest(text=text), self.assertRaises(helper.InstallError):
                helper.validate_requirements(text)

    def test_no_silent_omission_of_one_bad_entry(self):
        with self.assertRaises(helper.InstallError):
            helper.validate_requirements(EXPORT + "-e .\n")

    def test_pinned_build_backend_has_all_eighteen_wheel_hashes(self):
        text = (ROOT / helper.BUILD_REQUIREMENTS).read_text(encoding="utf-8")
        self.assertEqual(helper.validate_requirements(text), text)
        hashes = helper.re.findall(r"--hash=sha256:([0-9a-f]{64})", text)
        self.assertEqual(len(hashes), 18)
        self.assertEqual(len(set(hashes)), 18)
        self.assertIn("uv_build==0.9.30", text)
        self.assertIn("3cc10f63ebbf70fe2b57d7e85ce8928e36819e8c14c6c871833b4a47f3be96c5", hashes)
        self.assertNotIn("https://", text)
        self.assertIn("uv_build>=0.9.26,<0.10.0", (ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertIn("!packaging/build-requirements.txt", (ROOT / ".gitignore").read_text())


class IndexAndEnvironmentTest(unittest.TestCase):
    def test_accepts_only_https_simple_index_locations(self):
        for url in [INDEX, INDEX + "/", "https://packages.example.invalid:443/a/b/simple",
                    "https://[::1]:8443/simple", "https://packages.example.invalid/team%20name/simple"]:
            with self.subTest(url=url):
                self.assertEqual(helper.validate_index(url), url)
        for url in [None, "", "pypi", "./index", "http://example.invalid/simple", "file:///index",
                    "https://", "https://user:secret@example.invalid/simple", "https://user@example.invalid",
                    INDEX + "?token=secret", INDEX + "#secret", INDEX + "?", INDEX + "#",
                    " https://example.invalid", INDEX + "\n", "https://example.invalid\\@other.invalid",
                    "https://example.invalid:99999", "https://example.invalid:0", "https://example.invalid:",
                    "https://bad_host.invalid/simple", "https://-bad.invalid", "https://exa%6dple.invalid",
                    "https://[broken"]:
            with self.subTest(url=url), self.assertRaises(helper.InstallError) as error:
                helper.validate_index(url)
            self.assertNotIn("secret", str(error.exception))

    def test_removes_all_resolver_leakage_preserves_transport_auth_and_parent(self):
        parent = {
            "UV_DEFAULT_INDEX": INDEX, "UV_INDEX": "https://other.invalid", "UV_INDEX_URL": "other",
            "UV_EXTRA_INDEX_URL": "other", "UV_FIND_LINKS": "other", "UV_CONFIG_FILE": "other",
            "UV_NO_CONFIG": "0", "UV_CONSTRAINT": "other", "UV_OVERRIDE": "other",
            "UV_BUILD_CONSTRAINT": "other", "UV_NO_VERIFY_HASHES": "1", "UV_INSECURE_HOST": "*",
            "UV_SYSTEM_PYTHON": "1", "UV_PROJECT_ENVIRONMENT": "/unrelated", "UV_NO_DEV": "1",
            "UV_PYTHON": "other", "UV_ENV_FILE": "other", "PIP_INDEX_URL": "other",
            "PIP_EXTRA_INDEX_URL": "other", "PIP_TRUSTED_HOST": "*", "PIP_CONFIG_FILE": "other",
            "VIRTUAL_ENV": "/unrelated", "CONDA_PREFIX": "/unrelated", "PYTHONPATH": "/unrelated",
            "PYTHONHOME": "/unrelated", "__PYVENV_LAUNCHER__": "/unrelated", "PATH": "prerequisites",
            "HTTPS_PROXY": "http://user:secret@proxy.invalid:8080", "NO_PROXY": "local.invalid",
            "SSL_CERT_FILE": "managed-ca.pem", "UV_SYSTEM_CERTS": "true",
            "UV_KEYRING_PROVIDER": "subprocess", "UV_CREDENTIALS_DIR": "existing-auth",
            "UV_OFFLINE": "1", "HOME": "home",
        }
        original = parent.copy()
        env = helper.child_environment(parent)
        self.assertEqual(parent, original)
        for key in ("PATH", "HTTPS_PROXY", "NO_PROXY", "SSL_CERT_FILE", "UV_SYSTEM_CERTS",
                    "UV_KEYRING_PROVIDER", "UV_CREDENTIALS_DIR", "UV_OFFLINE", "HOME"):
            self.assertEqual(env[key], parent[key])
        allowed = helper.SAFE_UV_ENV | {"UV_NO_CONFIG", "UV_PYTHON_DOWNLOADS", "UV_NO_PROGRESS"}
        self.assertTrue(all(not key.startswith("UV_") or key in allowed for key in env))
        self.assertEqual(env["PIP_CONFIG_FILE"], os.devnull)
        self.assertEqual(env["UV_PYTHON_DOWNLOADS"], "never")
        for key in ("VIRTUAL_ENV", "CONDA_PREFIX", "PYTHONPATH", "PYTHONHOME"):
            self.assertNotIn(key, env)

    def test_runner_never_logs_subprocess_urls_or_credentials(self):
        root = Path.cwd()
        env = {"UV_OFFLINE": "1", "HTTPS_PROXY": "secret"}
        runner = helper.Runner(root, env)
        result = subprocess.CompletedProcess([], 17, "private signed URL secret", "password secret")
        with patch.object(helper.subprocess, "run", return_value=result) as run:
            with self.assertRaises(helper.InstallError) as error:
                runner.run(["uv", "pip", "install", "--default-index", INDEX], "Dependency install", offline=True)
        self.assertNotIn("secret", str(error.exception))
        self.assertNotIn(INDEX, str(error.exception))
        self.assertIn("exit 17", str(error.exception))
        kwargs = run.call_args.kwargs
        self.assertFalse(kwargs["shell"])
        self.assertTrue(kwargs["capture_output"])
        self.assertFalse(kwargs["check"])
        self.assertEqual(kwargs["env"]["UV_OFFLINE"], "1")
        self.assertEqual(kwargs["env"]["PIP_NO_INDEX"], "1")
        self.assertNotIn("PIP_NO_INDEX", env)

    def test_spawn_and_parser_errors_do_not_echo_sensitive_inputs(self):
        with patch.object(helper.subprocess, "run", side_effect=OSError("secret")):
            with self.assertRaises(helper.InstallError) as error:
                helper.Runner(Path.cwd(), {}).run(["uv"], "uv preflight")
            self.assertNotIn("secret", str(error.exception))
        with self.assertRaises(helper.InstallError) as error:
            helper.parser().parse_args(["--bad-secret-option"])
        self.assertNotIn("secret", str(error.exception))


class FakeRunner:
    """Emulates filesystem outcomes only; does not start Python, uv or a backend."""
    def __init__(self, root, env):
        self.root, self.env = root, env
        self.calls = []
        self.inputs = {}
        self.export = EXPORT
        self.fail = None
        self.mutate = None
        self.version = "uv 0.12.10 (offline test fixture)\n"
        self.python_version = [3, 10, 14]
        self.wheel_count = 1

    def run(self, argv, stage, *, offline=False):
        self.calls.append((list(argv), stage, offline))
        if stage == self.mutate:
            (self.root / "uv.lock").write_text("changed by a simulated concurrent writer")
        if stage == self.fail:
            raise helper.InstallError("Simulated subprocess failure")
        if stage == "uv preflight":
            return self.version
        if stage == "Python preflight":
            executable = Path(argv[0])
            is_venv = executable.parent.name in {"bin", "Scripts"}
            prefix = executable.parent.parent if is_venv else self.root / "base"
            return json.dumps({"version": self.python_version, "platform": "win32",
                               "prefix": str(prefix), "base_prefix": str(self.root / "base")})
        if stage in {"Offline lock check", "Frozen dependency export"}:
            return self.export
        if "virtualenv creation" in stage.lower():
            destination = Path(argv[argv.index("--python") + 2])
            destination.mkdir(parents=True, exist_ok=True)
            (destination / "pyvenv.cfg").write_text("home = test\n")
            python = helper.venv_python(destination)
            python.parent.mkdir(parents=True, exist_ok=True)
            python.touch()
        if "-r" in argv:
            self.inputs[stage] = Path(argv[argv.index("-r") + 1]).read_bytes()
        if stage == "Local project wheel build":
            output = Path(argv[argv.index("--out-dir") + 1])
            output.mkdir()
            for index in range(self.wheel_count):
                (output / f"bubble_buddy-0.1.{index}-py3-none-any.whl").write_bytes(b"test wheel bytes")
        return ""


class InstallWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="bb bootstrap tests ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        (self.root / "pyproject.toml").write_bytes((ROOT / "pyproject.toml").read_bytes())
        (self.root / "uv.lock").write_bytes(b"version = 1\n# mock export owns resolution\n")
        (self.root / "packaging").mkdir()
        (self.root / helper.BUILD_REQUIREMENTS).write_bytes((ROOT / helper.BUILD_REQUIREMENTS).read_bytes())
        self.host_python = self.root / "host-python"
        self.host_python.touch()
        self.runner = None
        self.configure = lambda runner: None
        self.original_lock = (self.root / "uv.lock").read_bytes()
        self.original_manifest = (self.root / "pyproject.toml").read_bytes()

    def factory(self, root, env):
        self.runner = FakeRunner(root, env)
        self.configure(self.runner)
        return self.runner

    def execute(self, *options, parent=None):
        args = helper.parser().parse_args(["--python", str(self.host_python), *options])
        with contextlib.redirect_stdout(io.StringIO()):
            helper.install(args, root=self.root,
                           parent_env={"UV_DEFAULT_INDEX": INDEX} if parent is None else parent,
                           runner_factory=self.factory)

    def call(self, stage):
        return next(item for item in self.runner.calls if item[1] == stage)

    def existing_env(self):
        target = self.root / ".venv"
        target.mkdir()
        (target / "pyvenv.cfg").write_text("home = test\n")
        executable = helper.venv_python(target)
        executable.parent.mkdir(parents=True)
        executable.touch()
        (target / "unrelated-package.txt").write_text("keep me")
        return target

    def test_full_workflow_exact_hashes_argv_local_build_and_immutable_lock(self):
        self.execute("--dev", "--venv", ".venv-spaces ü")
        self.assertEqual(self.runner.inputs["Locked dependency installation"], EXPORT.encode("utf-8"))
        for stage, flag in [("Offline lock check", "--locked"), ("Frozen dependency export", "--frozen")]:
            argv, _, offline = self.call(stage)
            self.assertTrue(offline)
            for required in (flag, "--offline", "--no-config", "--no-emit-project", "--no-default-groups",
                             "--group", "dev", "--no-header", "--no-annotate", "--no-python-downloads"):
                self.assertIn(required, argv)
            self.assertNotIn(INDEX, argv)
            self.assertNotIn("--no-hashes", argv)
        for stage in ("Locked dependency installation", "Pinned build-backend installation"):
            argv, _, offline = self.call(stage)
            self.assertFalse(offline)
            self.assertEqual(argv[argv.index("--default-index") + 1], INDEX)
            for required in ("--no-deps", "--require-hashes", "--only-binary", ":all:", "--no-config",
                             "--no-python-downloads", "--no-sources"):
                self.assertIn(required, argv)
            for forbidden in ("--extra-index-url", "--index", "--find-links", "--system", "--upgrade"):
                self.assertNotIn(forbidden, argv)
        backend = self.runner.inputs["Pinned build-backend installation"].decode("utf-8")
        self.assertIn("uv_build==0.9.30", backend)
        build, _, offline = self.call("Local project wheel build")
        self.assertTrue(offline)
        for required in ("--force-pep517", "--no-build-isolation", "--no-index", "--offline", "--wheel"):
            self.assertIn(required, build)
        self.assertNotIn(INDEX, build)
        local, _, offline = self.call("Local project installation")
        self.assertTrue(offline)
        self.assertIn("--no-deps", local)
        self.assertIn("--no-index", local)
        self.assertIn("--require-hashes", local)
        self.assertNotIn(INDEX, local)
        wheel_hash = hashlib.sha256(b"test wheel bytes").hexdigest()
        local_text = self.runner.inputs["Local project installation"].decode("utf-8")
        self.assertTrue(local_text.startswith("file:///"))
        self.assertIn(f"--hash=sha256:{wheel_hash}", local_text)
        self.assertEqual((self.root / "uv.lock").read_bytes(), self.original_lock)
        self.assertEqual((self.root / "pyproject.toml").read_bytes(), self.original_manifest)
        receipt = json.loads((self.root / ".venv-spaces ü/bubble-buddy-install.json").read_text())
        self.assertEqual(receipt["status"], "complete")
        self.assertEqual(receipt["project_wheel_sha256"], wheel_hash)
        self.assertTrue(receipt["project_installed"])
        self.assertTrue(receipt["dev"])
        self.assertNotIn(INDEX, json.dumps(receipt))
        self.assertFalse(any("sync" in argv or "run" in argv for argv, _, _ in self.runner.calls))

    def test_dependencies_only_skips_backend_project_and_dev_by_default(self):
        (self.root / helper.BUILD_REQUIREMENTS).unlink()
        self.execute("--dependencies-only")
        stages = [stage for _, stage, _ in self.runner.calls]
        self.assertNotIn("Pinned build-backend installation", stages)
        self.assertNotIn("Local project wheel build", stages)
        self.assertNotIn("Local project installation", stages)
        self.assertNotIn("--group", self.call("Frozen dependency export")[0])
        receipt = json.loads((self.root / ".venv/bubble-buddy-install.json").read_text())
        self.assertFalse(receipt["project_installed"])

    def test_requires_explicit_index_never_uses_pip_config_fallback(self):
        for parent in ({}, {"PIP_INDEX_URL": INDEX}, {"UV_INDEX_URL": INDEX}):
            with self.subTest(parent=parent), self.assertRaises(helper.InstallError):
                self.execute(parent=parent)
            self.assertIsNone(self.runner)
            self.assertFalse((self.root / ".venv").exists())

    def test_explicit_index_wins_and_offline_is_not_relaxed(self):
        parent = {"UV_DEFAULT_INDEX": "http://invalid.invalid", "UV_INDEX": "other",
                  "UV_OFFLINE": "1", "VIRTUAL_ENV": "unrelated", "PIP_EXTRA_INDEX_URL": "other"}
        original = parent.copy()
        self.execute("--index-url", INDEX, "--dependencies-only", parent=parent)
        self.assertEqual(parent, original)
        self.assertNotIn("UV_INDEX", self.runner.env)
        self.assertNotIn("UV_DEFAULT_INDEX", self.runner.env)
        self.assertNotIn("VIRTUAL_ENV", self.runner.env)
        self.assertNotIn("PIP_EXTRA_INDEX_URL", self.runner.env)
        self.assertEqual(self.runner.env["UV_OFFLINE"], "1")
        self.assertIn(INDEX, self.call("Locked dependency installation")[0])

    def test_existing_environment_requires_explicit_sync_and_never_recreates(self):
        target = self.existing_env()
        with self.assertRaisesRegex(helper.InstallError, "already exists"):
            self.execute()
        self.assertIsNone(self.runner)
        self.execute("--sync-existing", "--dependencies-only")
        self.assertNotIn("Virtualenv creation", [stage for _, stage, _ in self.runner.calls])
        self.assertIn("--reinstall", self.call("Locked dependency installation")[0])
        self.assertEqual((target / "unrelated-package.txt").read_text(), "keep me")
        self.assertEqual((target / "pyvenv.cfg").read_text(), "home = test\n")

    def test_existing_nonvenv_and_missing_sync_target_fail_before_subprocess(self):
        with self.assertRaises(helper.InstallError):
            self.execute("--sync-existing")
        (self.root / ".venv").mkdir()
        (self.root / ".venv/user-file").write_text("keep")
        with self.assertRaises(helper.InstallError):
            self.execute("--sync-existing")
        self.assertEqual((self.root / ".venv/user-file").read_text(), "keep")
        self.assertIsNone(self.runner)

    def test_paths_outside_checkout_root_and_git_are_rejected(self):
        for path in (".", "../foreign-venv", ".git/venv"):
            with self.subTest(path=path), self.assertRaises(helper.InstallError):
                self.execute("--venv", path)
        self.assertIsNone(self.runner)

    def test_symlinked_environment_is_not_followed(self):
        target = self.root / "actual-environment"
        target.mkdir()
        (target / "keep").write_text("unchanged")
        link = self.root / ".venv"
        try:
            link.symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Creating symlinks is not permitted on this host")
        with self.assertRaises(helper.InstallError):
            self.execute("--sync-existing")
        self.assertEqual((target / "keep").read_text(), "unchanged")
        self.assertIsNone(self.runner)

    def test_platform_path_conventions(self):
        path = Path("environment with spaces")
        self.assertEqual(helper.venv_python(path, True), path / "Scripts/python.exe")
        self.assertEqual(helper.venv_python(path, False), path / "bin/python")

    def test_old_uv_missing_python_or_old_python_fail_without_creation(self):
        self.configure = lambda runner: setattr(runner, "version", "uv 0.9.0\n")
        with self.assertRaisesRegex(helper.InstallError, "uv >=0.12.10"):
            self.execute()
        self.assertFalse((self.root / ".venv").exists())
        self.configure = lambda runner: setattr(runner, "python_version", [3, 9, 0])
        with self.assertRaises(helper.InstallError):
            self.execute()
        self.host_python.unlink()
        with self.assertRaises(helper.InstallError):
            self.execute()
        self.assertFalse((self.root / ".venv").exists())

    def test_stale_lock_bad_export_or_bad_backend_fails_before_environment_creation(self):
        for stage in ("Offline lock check", "Frozen dependency export"):
            self.configure = lambda runner, stage=stage: setattr(runner, "fail", stage)
            with self.assertRaises(helper.InstallError):
                self.execute()
            self.assertFalse((self.root / ".venv").exists())
            self.assertEqual((self.root / "uv.lock").read_bytes(), self.original_lock)
        self.configure = lambda runner: setattr(runner, "export", "-e .\n")
        with self.assertRaises(helper.InstallError):
            self.execute()
        self.assertFalse((self.root / ".venv").exists())
        self.configure = lambda runner: None
        (self.root / helper.BUILD_REQUIREMENTS).write_text("uv_build>=0.9\n")
        with self.assertRaises(helper.InstallError):
            self.execute()
        self.assertFalse((self.root / ".venv").exists())

    def test_changed_lock_aborts_immediately_without_install_or_restore(self):
        self.configure = lambda runner: setattr(runner, "mutate", "Frozen dependency export")
        with self.assertRaisesRegex(helper.InstallError, "inputs changed"):
            self.execute()
        self.assertFalse((self.root / ".venv").exists())
        self.assertNotIn("Locked dependency installation", [stage for _, stage, _ in self.runner.calls])
        self.assertNotEqual((self.root / "uv.lock").read_bytes(), self.original_lock)

    def test_failure_short_circuits_and_preserves_existing_environment(self):
        target = self.existing_env()
        for stage in ("Locked dependency installation", "Pinned build-backend installation",
                      "Local project wheel build", "Local project installation", "Dependency consistency check"):
            self.configure = lambda runner, stage=stage: setattr(runner, "fail", stage)
            with self.subTest(stage=stage), self.assertRaises(helper.InstallError):
                self.execute("--sync-existing")
            self.assertEqual(self.runner.calls[-1][1], stage)
            self.assertEqual((target / "unrelated-package.txt").read_text(), "keep me")
            receipt = json.loads((target / "bubble-buddy-install.json").read_text())
            self.assertEqual(receipt["status"], "incomplete")
            self.assertEqual((self.root / "uv.lock").read_bytes(), self.original_lock)

    def test_missing_or_multiple_local_wheels_fail_closed(self):
        self.existing_env()
        for count in (0, 2):
            self.configure = lambda runner, count=count: setattr(runner, "wheel_count", count)
            with self.subTest(count=count), self.assertRaises(helper.InstallError):
                self.execute("--sync-existing")
            self.assertNotIn("Local project installation", [stage for _, stage, _ in self.runner.calls])

    def test_explicit_cache_path_is_passed_without_reading_global_cache_config(self):
        cache = self.root / "validation cache"
        self.execute("--dependencies-only", "--cache-dir", str(cache))
        for argv, stage, _ in self.runner.calls:
            if argv[0] == "uv" and stage != "uv preflight":
                self.assertEqual(argv[argv.index("--cache-dir") + 1], str(cache))


class PackagingContractTest(unittest.TestCase):
    def test_managed_packaging_uses_selected_python_and_no_process_killing(self):
        windows = (ROOT / "packaging/build.ps1").read_text(encoding="utf-8")
        self.assertIn("[switch]$NoSync", windows)
        self.assertIn("if (-not $NoSync -and -not $SkipStopProcesses)", windows)
        self.assertIn("& $Python -m PyInstaller", windows)
        self.assertIn("uv run pyinstaller", windows)  # Public default is retained.
        mac = (ROOT / "packaging/build_macos.sh").read_text(encoding="utf-8")
        self.assertIn('--no-sync)', mac)
        self.assertIn('PYTHON_COMMAND=("$ENV_PYTHON")', mac)
        self.assertIn('PYINSTALLER_COMMAND=("$ENV_PYTHON" -m PyInstaller)', mac)
        self.assertIn('"${PYINSTALLER_COMMAND[@]}" packaging/', mac)
        self.assertIn('"${PYTHON_COMMAND[@]}" -c', mac)
        self.assertIn("from importlib.metadata import version", mac)
        self.assertNotIn("import tomllib", mac)


if __name__ == "__main__":
    unittest.main()
