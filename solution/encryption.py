
import base64
import hashlib
import hmac
from pathlib import Path
from cryptography.fernet import Fernet

# Generate a secret key once and reuse it
KEY_FILE = Path(__file__).parent / ".secret.key"

if KEY_FILE.exists():
    SECRET_KEY = KEY_FILE.read_bytes()
else:
    SECRET_KEY = Fernet.generate_key()
    KEY_FILE.write_bytes(SECRET_KEY)

cipher = Fernet(SECRET_KEY)


def encrypt_text(value):
    """Encrypt personal information."""
    if value is None:
        return None
    return cipher.encrypt(str(value).encode()).decode()


def decrypt_text(value):
    """Decrypt previously encrypted information."""
    if value is None:
        return None
    return cipher.decrypt(value.encode()).decode()


def hash_phone(phone):
    """Create a keyed hash for phone matching."""
    key = base64.urlsafe_b64decode(SECRET_KEY)
    return hmac.new(
        key,
        phone.encode(),
        hashlib.sha256
    ).hexdigest()
