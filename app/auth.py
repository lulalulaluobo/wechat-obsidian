from __future__ import annotations

import hashlib
import hmac
import secrets


SESSION_COOKIE_NAME = "wechat_md_session"
PASSWORD_SCHEME = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 200_000


def hash_password(password: str, salt: str | None = None) -> str:
    normalized = password or ""
    password_salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        normalized.encode("utf-8"),
        password_salt.encode("utf-8"),
        PASSWORD_ITERATIONS,
    )
    return f"{PASSWORD_SCHEME}${PASSWORD_ITERATIONS}${password_salt}${digest.hex()}"


def verify_password(password: str, stored_hash: str | None) -> bool:
    if not stored_hash:
        return False
    try:
        scheme, iterations_text, salt, expected = stored_hash.split("$", 3)
        if scheme != PASSWORD_SCHEME:
            return False
        iterations = int(iterations_text)
    except (TypeError, ValueError):
        return False

    digest = hashlib.pbkdf2_hmac(
        "sha256",
        (password or "").encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    ).hex()
    return hmac.compare_digest(digest, expected)


def generate_session_secret() -> str:
    return secrets.token_urlsafe(32)


def build_session_token(username: str, password_hash: str, session_secret: str) -> str:
    identity = f"{username}:{password_hash}"
    signature = hmac.new(
        session_secret.encode("utf-8"),
        identity.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{username}:{signature}"


def verify_session_token(
    token: str | None,
    username: str,
    password_hash: str,
    session_secret: str,
) -> bool:
    if not token:
        return False
    try:
        token_username, provided_signature = token.split(":", 1)
    except ValueError:
        return False
    if token_username != username:
        return False
    expected_token = build_session_token(username, password_hash, session_secret)
    return hmac.compare_digest(token, expected_token)
