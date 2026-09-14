"""OS-protected credential persistence shared by account-based providers."""
from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any


@contextmanager
def locked_store(path: Path, thread_lock: Any):
    from filelock import FileLock
    from msal_extensions import build_encrypted_persistence

    with thread_lock:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            persistence = build_encrypted_persistence(str(path))
        except Exception:
            raise RuntimeError("OS-protected credential storage is unavailable; no plaintext fallback.") from None
        # OS locks release on process exit, including a crash during renewal.
        with FileLock(str(path) + ".lock", timeout=45):
            yield persistence


def load_credentials(store: Any, provider: str) -> dict[str, Any]:
    from msal_extensions.persistence import PersistenceNotFound

    try:
        value = json.loads(store.load())
    except PersistenceNotFound:
        return {}
    except Exception:
        raise RuntimeError(f"Cannot read protected {provider} credentials. Sign in again to replace them.") from None
    if not isinstance(value, dict):
        raise RuntimeError(f"Invalid {provider} credential store. Sign in again.")
    return value


def save_credentials(store: Any, credentials: dict[str, Any], provider: str) -> None:
    from msal_extensions import FilePersistenceWithDataProtection

    temporary = None
    try:
        payload = json.dumps(credentials)
        if isinstance(store, FilePersistenceWithDataProtection):
            # DPAPI protects by OS user, not filename. Replace atomically to
            # avoid leaving a truncated credential file after a crash.
            destination = Path(store.get_location())
            fd, temporary = tempfile.mkstemp(dir=destination.parent, prefix=".account-auth-")
            os.close(fd)
            FilePersistenceWithDataProtection(temporary).save(payload)
            os.replace(temporary, destination)
        else:
            store.save(payload)  # native keychain/secret-service item update
    except Exception:
        raise RuntimeError(f"Could not save {provider} credentials in OS-protected storage.") from None
    finally:
        if temporary:
            Path(temporary).unlink(missing_ok=True)
