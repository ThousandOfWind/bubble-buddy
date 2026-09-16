#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

EDITION="azure"
VERSION=""
SKIP_DMG=0
NO_SYNC=0
ENV_PYTHON=""
SIGN_IDENTITY="-"

usage() {
  cat <<'EOF'
Build the macOS Bubble Buddy app bundle and optional DMG.

Usage:
  packaging/build_macos.sh [--edition azure|full] [--version X.Y.Z] [--skip-dmg] [--sign-identity IDENTITY] [--no-sync] [--python VENV_PYTHON]

Examples:
  packaging/build_macos.sh
  packaging/build_macos.sh --edition full
  packaging/build_macos.sh --version 0.2.0 --sign-identity "Developer ID Application: ..."
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --edition)
      EDITION="${2:-}"
      shift 2
      ;;
    --version)
      VERSION="${2:-}"
      shift 2
      ;;
    --skip-dmg)
      SKIP_DMG=1
      shift
      ;;
    --no-sync)
      NO_SYNC=1
      shift
      ;;
    --python)
      ENV_PYTHON="${2:-}"
      shift 2
      ;;
    --sign-identity)
      SIGN_IDENTITY="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ "$EDITION" != "azure" && "$EDITION" != "full" ]]; then
  echo "--edition must be 'azure' or 'full'" >&2
  exit 2
fi

PYTHON_COMMAND=(uv run python)
PYINSTALLER_COMMAND=(uv run pyinstaller)
if [[ "$NO_SYNC" == "1" ]]; then
  ENV_PYTHON="${ENV_PYTHON:-$ROOT/.venv/bin/python}"
  if [[ ! -x "$ENV_PYTHON" ]]; then
    echo "Prepared Python missing. Run tools/install_from_index.py --dev first." >&2
    exit 1
  fi
  PYTHON_COMMAND=("$ENV_PYTHON")
  PYINSTALLER_COMMAND=("$ENV_PYTHON" -m PyInstaller)
elif [[ -n "$ENV_PYTHON" ]]; then
  echo "--python requires --no-sync (use an already prepared virtualenv)." >&2
  exit 2
fi

if [[ -z "$VERSION" ]]; then
  # Metadata lookup works on Python 3.10 and does not import the app.
  VERSION="$("${PYTHON_COMMAND[@]}" -c "from importlib.metadata import version; print(version('bubble-buddy'))")"
fi

export BB_VERSION="$VERSION"
if [[ "$EDITION" == "full" ]]; then
  export BB_INCLUDE_LOCAL=1
  EDITION_SUFFIX="-Full"
  DEFAULT_BACKEND="mlx"
  DEFAULT_POLISH_ENGINE="rules"
  DEFAULT_MLX_MODEL="mlx-community/whisper-large-v3-turbo"
else
  export BB_INCLUDE_LOCAL=0
  EDITION_SUFFIX=""
  DEFAULT_BACKEND="azure"
  DEFAULT_POLISH_ENGINE="azure"
  DEFAULT_MLX_MODEL=""
fi

BUNDLED_CONFIG_DIR="build/macos-config/$EDITION"
mkdir -p "$BUNDLED_CONFIG_DIR"
export BB_BUNDLED_CONFIG="$ROOT/$BUNDLED_CONFIG_DIR/config.json"
cat > "$BB_BUNDLED_CONFIG" <<EOF
{
  "backend": "$DEFAULT_BACKEND",
  "mlx_model": "$DEFAULT_MLX_MODEL",
  "polish": "auto",
  "polish_engine": "$DEFAULT_POLISH_ENGINE",
  "ui_language": "auto",
  "start_collapsed": true,
  "show_setup_on_first_launch": true,
  "azure": {
    "auth": "aad"
  }
}
EOF

echo "==> Building macOS app ($EDITION edition, version $VERSION)"
"${PYINSTALLER_COMMAND[@]}" packaging/bubble-buddy-macos.spec --noconfirm \
  --distpath dist/macos --workpath build/pyi-macos

APP="dist/macos/Bubble Buddy.app"
if [[ ! -d "$APP" ]]; then
  echo "Expected app bundle missing: $APP" >&2
  exit 1
fi

if command -v codesign >/dev/null 2>&1; then
  echo "==> Codesigning app (identity: $SIGN_IDENTITY)"
  codesign --force --deep --sign "$SIGN_IDENTITY" "$APP"
else
  echo "==> codesign not found; leaving app unsigned"
fi

if [[ "$SKIP_DMG" == "1" ]]; then
  echo "==> Done: $APP"
  exit 0
fi

STAGE="build/dmg/Bubble Buddy"
DMG="dist/installer/BubbleBuddy${EDITION_SUFFIX}-${VERSION}.dmg"
rm -rf "$STAGE"
mkdir -p "$STAGE" "dist/installer"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
rm -f "$DMG"

echo "==> Creating DMG: $DMG"
hdiutil create -volname "Bubble Buddy" -srcfolder "$STAGE" -ov -format UDZO "$DMG"

echo "==> Done. App: $APP"
echo "==> Done. DMG: $DMG"
