# Releasing a new version

Releases are automated by [`.github/workflows/release.yml`](../.github/workflows/release.yml):
pushing a `vX.Y.Z` tag builds **both** editions on a Windows runner and publishes
them as a GitHub Release with both installers attached.

```bash
# 1. bump the version in pyproject.toml, commit, then:
git tag v0.2.0
git push origin v0.2.0
```

The workflow then: installs uv + Python + Inno Setup, runs `packaging\build.ps1`
twice (`-Edition azure` then `-Edition full`, stamping the tag version), and attaches
`BubbleBuddy-Setup-0.2.0.exe` and `BubbleBuddy-Full-Setup-0.2.0.exe` to the release.
You can also trigger it manually from the **Actions** tab (workflow_dispatch) to test
a build without tagging.

> **Note:** `main` is protected — all changes must land through a pull request
> (Copilot review is requested automatically and all review threads must be
> resolved before merge). Push tags to trigger releases, not commits to `main`.
