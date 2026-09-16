# Installing through an approved Python package index

Use this **opt-in source-install path** when your device policy requires a
protected package feed. The normal public `uv sync` workflow is unchanged.
An index setting alone is not enough to redirect the artifact URLs already
recorded in a frozen `uv.lock`. uv also does **not** read pip's `pip.ini` or
`pip.conf`.

## Prerequisites and first install

- A source checkout, an already installed **Python 3.10+**, and **uv >=0.12.10**.
  The helper never downloads Python or uv. Run it with ordinary Python, **not
  `uv run`** (which could sync before the helper starts).
- Obtain the approved **HTTPS simple-index URL** from your administrator. Neither
  the code nor this guide chooses a feed or establishes its approval for you.
- Use a URL without username/password, query parameters or fragments. Keep
  credentials out of arguments, source files, screenshots and logs.

PowerShell, after replacing the placeholder with your approved URL:

```powershell
$env:UV_DEFAULT_INDEX = '<approved HTTPS simple-index URL>'
python tools/install_from_index.py --dev
```

macOS, using your installed Python:

```bash
export UV_DEFAULT_INDEX='<approved HTTPS simple-index URL>'
python3 tools/install_from_index.py --dev
```

Alternatively pass `--index-url '<approved HTTPS simple-index URL>'`; this takes
precedence over `UV_DEFAULT_INDEX`. Missing/invalid input is an error, not a
fallback to PyPI. These examples set the current shell's environment only; the
helper does not modify pip/uv configuration, OS trust, proxy or security policy.
`--dev` includes the locked PyInstaller dependency group; omit it for runtime
packages only. Azure/Full edition selection does not change these dependency sets.

By default the helper creates `.venv` with the Python running the helper. To use
another **existing interpreter**, pass `--python /path/to/python` (Windows paths
with spaces should be quoted). This option applies only to new environments.

## Existing environments and controlled checks

The helper refuses an existing target by default. It never clears, deletes or
recreates an existing environment, and ignores unrelated active virtualenvs.
Choose a new path inside the checkout, or explicitly authorize updating the
locked packages in the old environment:

```text
python tools/install_from_index.py --dev --venv .venv-validation
python tools/install_from_index.py --dev --sync-existing
```

`--sync-existing` reinstalls the selected locked dependency closure with hash
checking, including already-satisfied versions. It can upgrade **or downgrade
those named packages** to the lock; it does not prune unrelated packages. It
uses that environment's interpreter, not `--python`. Do not run concurrent
installers or update an environment while its application is running. A failed
install can leave a new/explicitly updated environment incomplete; there is no
transactional rollback. Choose a fresh environment rather than automatically
removing packages to repair conflicts.

For controlled dependency acquisition only:

```text
python tools/install_from_index.py --dev --dependencies-only --venv .venv-validation --cache-dir build/validation-cache
```

This **does not install Bubble Buddy or its build backend**. To complete that
same environment, omit `--dependencies-only` and add `--sync-existing --venv
.venv-validation` (and the same optional cache path). A full install is needed
before using the app or the packaging entry points.

The default cache is a new temporary, helper-owned cache, removed after the
operation. `--cache-dir PATH` opts into a reusable cache without changing uv's
normal cache settings. A real cold-cache check requires both a **new environment
and an empty cache**; an existing cache with `--refresh` is not the same evidence.
A small `bubble-buddy-install.json` inside the venv records completion, selected
groups and input/wheel hashes, without the index URL. Failed package stages leave
it `incomplete`; dependency-only completion has `project_installed: false`.

## How integrity and routing are preserved

1. Check lock freshness with an offline locked export, then use offline
   `uv export --frozen --no-emit-project` to obtain the exact runtime/dev pins,
   platform/Python markers and SHA-256 hashes. No relocking, version changes,
   source-URL rewriting or second committed runtime lock is involved.
2. Accept only exact registry requirements with hashes. Direct URLs, VCS,
   editable/local dependencies, unpinned/wildcard versions, nested requirement
   files, embedded index options and unsupported marker syntax fail closed.
   Nothing is silently stripped or converted to another source.
3. Install that full closure with `uv pip install --no-deps --require-hashes
   --only-binary :all:` and the supplied `--default-index`. A missing original
   wheel/hash, incompatible platform or feed rejection stops the operation.
   There is **no extra/public index, sdist-build or unpinned retry**.
4. Build the local project separately. `uv.lock` does not pin the PEP 517 backend;
   [`packaging/build-requirements.txt`](../packaging/build-requirements.txt) pins
   `uv_build==0.9.30` and its wheel hashes. It has no runtime dependency closure.
   The backend is installed through the same index in a temporary build venv.
   The local wheel is built with `--offline --no-build-isolation --force-pep517`,
   then installed offline with its own hash and `--no-deps`. The owned local
   wheel is the only intentional direct-file requirement. It is **not editable**:
   rerun the helper explicitly after changing source.
5. Check installed dependency metadata and ensure `pyproject.toml`, `uv.lock`
   and the pinned backend requirements did not change during installation.

Every uv stage disables config discovery and Python downloads. Child processes
ignore inherited UV_/PIP_ alternate indexes, find-links, constraints/overrides,
installation targets, group settings and hash/trust-disable flags; pip config
loading is also disabled for those children. The parent environment and config
files are not rewritten. Standard transport variables such as `HTTPS_PROXY`,
`NO_PROXY`, `SSL_CERT_FILE` and `SSL_CERT_DIR` remain in effect. `UV_SYSTEM_CERTS`
(or its legacy `UV_NATIVE_TLS` alias), `UV_OFFLINE`, `UV_KEYRING_PROVIDER` and
`UV_CREDENTIALS_DIR` are preserved. Use only approved existing uv authentication
(e.g. normal uv auth storage/netrc or an explicitly configured keyring provider);
the helper neither obtains credentials nor converts pip credentials/configuration.
An inherited offline restriction is never relaxed.

Installer stdout/stderr is captured, not echoed, because it can contain private
feed paths, proxy credentials or signed redirect URLs. Failures identify the
stage and exit code. Review detailed diagnostics only in an appropriately
private, authorized support context—never paste credential-bearing output into
a public issue.

**Limits:** a single index is not an egress sandbox. Feed artifact links and
redirects must themselves use policy-approved delivery paths. Hashes establish
artifact integrity, not endpoint approval. uv offline flags do not sandbox
arbitrary build-backend/PyInstaller-hook code. Keep existing device controls and
verify real delivery telemetry; do not disable trust or security controls to
make an install pass. If a feed repackages a wheel so its hash differs from the
lock, stop rather than replacing the committed hash.

The helper works with Windows/macOS paths and Python 3.10+, but cannot add missing
wheels or change the project's platform support. In particular the current
Darwin dependency graph selects MLX, whose locked wheels require Apple Silicon
and macOS 14+; this path is not a fix for Intel Mac compatibility. Other native
packages also impose OS/CPU/Python limits. Model-weight downloads, app permissions,
authentication and audio/paste operation are separate tasks.

## Run and package without another sync

For the default `.venv`, replace `uv run ...` with **`uv run --no-sync ...`** after
successful setup. `--frozen` alone still syncs and can revisit public artifact
URLs. Do not add `--with`, isolated-environment or interpreter-download options.
For additional protection against interpreter provisioning, include
`--no-python-downloads` when using uv, or use the prepared executable directly.
With a custom venv, use its explicit executable rather than relying on uv's
project-environment discovery.

```powershell
# Uses the prepared .venv and does NOT stop running applications.
pwsh -File packaging/build.ps1 -NoSync -SkipInstaller
# A custom prepared venv:
pwsh -File packaging/build.ps1 -NoSync -Python .venv-validation\Scripts\python.exe -SkipInstaller
```

```bash
packaging/build_macos.sh --no-sync --skip-dmg
packaging/build_macos.sh --no-sync --python .venv-validation/bin/python --skip-dmg
```

These switches use the selected Python directly (`-m PyInstaller`), without
provisioning or synchronization; missing dependencies are errors. macOS version
lookup uses installed package metadata and works on Python 3.10 without importing
the app. The existing signing/DMG behavior is unchanged. In the Windows wrapper,
**the normal default still stops running Bubble Buddy processes**; `-NoSync`
skips that behavior, or `-SkipStopProcesses` disables it for the normal workflow.
Native installer tooling must already be available as documented in the
[Windows](packaging.md) and [macOS](macos-packaging.md) packaging guides.

Verification requires **two separate Python processes** from the checkout,
using the prepared environment. Both commands are mandatory:

```text
uv run --no-sync python -m unittest discover -s tests -p install_from_index_cases.py -v
uv run --no-sync python -m unittest discover -s tests -v
```

For a custom venv, replace `uv run --no-sync python` with its explicit Python
executable in both commands. The standalone bootstrap cases need only stdlib
Python; the application/support suite needs the installed app dependencies.
At this revision these suites contain 26 and 282 cases respectively. The default
`test_*.py` discovery intentionally does not include `install_from_index_cases.py`.
Do not import those cases or launch their runner from a GUI test module. CI runs
them as a separate mandatory step in the same `unittest` job, so both process
exits gate success. An `OK` summary followed by a native shutdown crash is a
failure, not a pass. No Qt/application lifecycle behavior or dependencies are
changed by this test topology.

Mocked unit checks do not prove cold-cache installation, actual feed routing,
package completeness or a successful native build. Validate those separately;
never treat installer success as permission to launch/record/sign in to the app.
