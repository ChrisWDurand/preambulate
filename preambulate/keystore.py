"""
Preambulate — encryption key management.

Keys are stored in ~/.preambulate/ identified by the project UUID
(from .preambulate_id in the project root). Each project has its own
Fernet key. The server never sees this key — it is used only to
encrypt before push and decrypt after pull.

Key file: ~/.preambulate/{project_id}.key
"""

from __future__ import annotations

from pathlib import Path

from cryptography.fernet import Fernet


def _key_dir() -> Path:
    d = Path.home() / ".preambulate"
    d.mkdir(mode=0o700, exist_ok=True)
    return d


def _key_path(project_id: str) -> Path:
    return _key_dir() / f"{project_id}.key"


def key_exists(project_id: str) -> bool:
    return _key_path(project_id).exists()


def generate_key(project_id: str) -> bytes:
    """Generate and persist a new Fernet key. Raises if key already exists."""
    path = _key_path(project_id)
    if path.exists():
        raise FileExistsError(f"key already exists: {path}")
    key = Fernet.generate_key()
    path.write_bytes(key)
    path.chmod(0o600)
    return key


def load_key(project_id: str) -> bytes:
    """Load the Fernet key for this project. Raises if not found."""
    path = _key_path(project_id)
    if not path.exists():
        raise FileNotFoundError(
            f"no encryption key at {path} — run 'preambulate init' first"
        )
    return path.read_bytes()


def replace_key(project_id: str, new_key: bytes) -> None:
    """Overwrite the key file with a new key value."""
    path = _key_path(project_id)
    path.write_bytes(new_key)
    path.chmod(0o600)


def _api_key_path() -> Path:
    return _key_dir() / "api_key"


def load_api_key() -> str:
    """Load the API key from ~/.preambulate/api_key. Returns '' if not found."""
    path = _api_key_path()
    if not path.exists():
        return ""
    return path.read_text().strip()


def save_api_key(key: str) -> None:
    """Persist the API key to ~/.preambulate/api_key (0o600)."""
    path = _api_key_path()
    path.write_text(key.strip())
    path.chmod(0o600)


def encrypt(project_id: str, data: bytes) -> bytes:
    """Encrypt data with this project's key."""
    return Fernet(load_key(project_id)).encrypt(data)


def decrypt(project_id: str, token: bytes) -> bytes:
    """Decrypt a Fernet token with this project's key."""
    return Fernet(load_key(project_id)).decrypt(token)
