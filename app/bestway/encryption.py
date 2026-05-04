"""AES-256-CBC encryption for Bestway AWS IoT commands.

Algorithm reverse-engineered from com/rongwei/library/utils/AESEncrypt.java:
  - Key:  SHA-256("{sign},{app_secret}")[:32] as UTF-8 bytes
  - IV:   Fixed 16-byte array hardcoded in the APK
  - Output: Base64(IV + ciphertext)
"""

from __future__ import annotations

import base64
import hashlib

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

# Fixed IV extracted from decompiled APK — never changes.
_FIXED_IV = bytes(
    [56, 110, 58, 168, 76, 255, 94, 159, 237, 215, 171, 181, 150, 40, 74, 166]
)


def encrypt_command(sign: str, app_secret: str, plaintext: str) -> str:
    """Encrypt a command payload.

    Args:
        sign:       MD5 signature from the current request (uppercase hex).
        app_secret: APP_SECRET constant from the APK.
        plaintext:  Already-serialised JSON string of the command.

    Returns:
        Base64-encoded string: Base64(IV + ciphertext).
    """
    key_material = f"{sign},{app_secret}".encode("utf-8")
    key = hashlib.sha256(key_material).hexdigest()[:32].encode("utf-8")

    cipher = AES.new(key, AES.MODE_CBC, _FIXED_IV)
    ciphertext = cipher.encrypt(pad(plaintext.encode("utf-8"), AES.block_size))

    return base64.b64encode(_FIXED_IV + ciphertext).decode("utf-8")
