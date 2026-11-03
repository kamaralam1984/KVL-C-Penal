"""Streaming AES-256-GCM file encryption for backups (cryptography library).

Format: b"KVLB1\\n" | 8-byte random nonce prefix | repeated [4-byte big-endian length | ciphertext+tag].
Per-chunk nonce = prefix || 4-byte counter; AAD is a single byte: 1 for the final chunk, else 0, so truncation and
reordering are detected. The key never leaves the agent host: it lives in <data_dir>/keys/backup.key (mode 0600) and is
referenced elsewhere only by its fingerprint (`key_id`). Losing this key makes encrypted backups unrecoverable.
"""
import base64
import hashlib
import os
import struct
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from kvl_agent.safe_paths import OpError

MAGIC = b"KVLB1\n"
CHUNK = 1024 * 1024


def key_fingerprint(key: bytes) -> str:
    return hashlib.sha256(b"kvl-backup-key:" + key).hexdigest()[:16]


def create_key(path: Path) -> str:
    """Create a new key file; refuses to overwrite an existing key."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = AESGCM.generate_key(256)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        raise OpError("key_exists", "A backup key already exists; it is never overwritten.") from None
    with os.fdopen(fd, "wb") as f:
        f.write(base64.urlsafe_b64encode(key))
    return key_fingerprint(key)


def load_key(path: Path) -> bytes:
    try:
        raw = base64.urlsafe_b64decode(path.read_bytes().strip())
    except (OSError, ValueError):
        raise OpError("key_missing", "Backup encryption key is not configured on this server.") from None
    if len(raw) != 32:
        raise OpError("key_invalid", "Backup encryption key is malformed.")
    return raw


def _chunks(f):
    prev = f.read(CHUNK)
    while True:
        nxt = f.read(CHUNK)
        yield prev, not nxt
        if not nxt:
            return
        prev = nxt


def encrypt_file(src: Path, dst: Path, key: bytes) -> None:
    aes, prefix = AESGCM(key), os.urandom(8)
    with open(src, "rb") as f, open(dst, "wb") as out:
        out.write(MAGIC + prefix)
        for i, (blk, final) in enumerate(_chunks(f)):
            ct = aes.encrypt(prefix + struct.pack(">I", i), blk, b"\x01" if final else b"\x00")
            out.write(struct.pack(">I", len(ct)) + ct)


def decrypt_file(src: Path, dst: Path, key: bytes) -> None:
    aes = AESGCM(key)
    with open(src, "rb") as f:
        if f.read(len(MAGIC)) != MAGIC:
            raise OpError("not_encrypted", "File is not a KVL encrypted backup.")
        prefix = f.read(8)
        i, saw_final = 0, False
        with open(dst, "wb") as out:
            while True:
                hdr = f.read(4)
                if not hdr:
                    break
                if saw_final or len(hdr) != 4:
                    raise OpError("decrypt_failed", "Encrypted backup is corrupt.")
                (n,) = struct.unpack(">I", hdr)
                ct = f.read(n)
                if len(ct) != n or n > CHUNK + 64:
                    raise OpError("decrypt_failed", "Encrypted backup is truncated.")
                nonce = prefix + struct.pack(">I", i)
                try:
                    out.write(aes.decrypt(nonce, ct, b"\x01"))
                    saw_final = True
                except InvalidTag:
                    try:
                        out.write(aes.decrypt(nonce, ct, b"\x00"))
                    except InvalidTag:
                        raise OpError("decrypt_failed", "Wrong key or tampered backup.") from None
                i += 1
    if not saw_final:
        raise OpError("decrypt_failed", "Encrypted backup is truncated.")
