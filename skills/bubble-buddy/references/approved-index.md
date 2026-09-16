# Source installation through an approved package feed

Use this lane only when the user is installing **from a source checkout** and
has explicitly supplied an approved HTTPS Python simple-index URL. It is not a
model-download fix or permission to change device security/network policy.
Confirm the platform/route through [install intake](install.md) first.

The repository's `tools/install_from_index.py` requires already installed Python
3.10+ and uv >=0.12.10. It is **not included in an installed copy of this skill**;
verify the user's actual source checkout contains it. Do not invent the helper
in an older checkout or run `uv run` to bootstrap it. See the repository's
[full source-install guide](https://github.com/ThousandOfWind/bubble-buddy/blob/main/docs/approved-index.md).

Once the approved URL is supplied via `UV_DEFAULT_INDEX`, from the checkout:

```text
python tools/install_from_index.py --dev
```

On macOS, use the installed `python3` if that is its command. An explicit
`--index-url '<approved HTTPS simple-index URL>'` overrides the environment.
Do not put credentials, query strings or fragments in the URL; the helper
rejects them. Do not copy an internal feed address into an issue or committed
file. uv does not read pip.ini/pip.conf; the helper does not discover or modify
those files. It preserves standard approved proxy/CA settings and uv auth
mechanisms, but ignores inherited alternative indexes, find-links and installer
configuration. Missing input, hash mismatch or missing wheels cause failure,
not a public-index retry.

The helper exports the frozen lock offline with exact pins/markers/hashes,
installs only hash-checked registry wheels from the supplied index, and builds
the local project separately using a pinned, hashed backend from that same
index. The public `uv.lock` is unchanged. A feed can still return artifact
links/redirects to other hosts: the administrator must ensure those delivery
paths are approved. Neither index selection nor offline flags are a sandbox.
Never disable TLS checks, change VPN/proxy/firewall settings or suggest another
mirror to bypass policy.

- **Existing env:** `.venv` is refused by default. Offer a new checkout-local
  `--venv .venv-validation`, or obtain explicit permission to use
  `--sync-existing`. That reinstalls the locked closure; it never clears the
  env or prunes unrelated packages. A failure can leave an incomplete env—do
  not silently delete it or call the installation complete.
- **Validation-only:** `--dependencies-only` omits the local project/backend;
  it does not produce a ready-to-run app. `--dev` includes PyInstaller. An
  optional `--cache-dir` selects a private cache for controlled checks; without
  it, a fresh temporary cache is used.
- **After setup:** use **`uv run --no-sync ...`** for the default `.venv`, or
  the custom env's executable directly. Plain `uv run` (even with `--frozen`)
  can sync public artifact URLs again. Do not add `--with`/isolated-env options.
- **Packaging:** `packaging/build.ps1 -NoSync` (PowerShell `-File` invocation)
  or `packaging/build_macos.sh --no-sync` uses the prepared Python, with optional
  `-Python` / `--python` for a custom env. Windows `-NoSync` also skips the
  wrapper's existing process-kill step. Normal packaging defaults are unchanged.
- **Limits:** source installation is non-editable; changes require explicitly
  rerunning the helper. Current Darwin MLX pins require Apple Silicon/macOS 14+;
  this route does not add Intel compatibility or missing native wheels. App,
  audio, account, model-weight and paste tests require their separate consent.

For failures, report the failing helper stage/exit code and distinguish
installation from validation. Installer output is withheld because it may
contain signed URLs or credentials. Use approved private support for any deeper
diagnostics; never echo raw config or logs into a public reply.
