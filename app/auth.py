from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import threading
import time
from collections import deque
from typing import Deque

from cryptography.fernet import Fernet, InvalidToken


SESSION_COOKIE_NAME = "wechat_md_session"
PASSWORD_SCHEME = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 200_000
APP_MASTER_KEY_ENV = "WECHAT_MD_APP_MASTER_KEY"
ADMIN_USERNAME_ENV = "WECHAT_MD_ADMIN_USERNAME"
ADMIN_PASSWORD_ENV = "WECHAT_MD_ADMIN_PASSWORD"
SESSION_COOKIE_SECURE_ENV = "WECHAT_MD_SESSION_COOKIE_SECURE"
LOGIN_FAILURE_WINDOW_SECONDS = 10 * 60
LOGIN_FAILURE_THRESHOLD = 5
LOGIN_LOCK_SECONDS = 15 * 60

_LOGIN_ATTEMPTS: dict[str, Deque[float]] = {}
_LOGIN_LOCKS: dict[str, float] = {}
_LOGIN_LOCK = threading.Lock()


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
        token_username, _provided_signature = token.split(":", 1)
    except ValueError:
        return False
    if token_username != username:
        return False
    expected_token = build_session_token(username, password_hash, session_secret)
    return hmac.compare_digest(token, expected_token)


def session_cookie_secure_enabled() -> bool:
    return _as_bool(os.environ.get(SESSION_COOKIE_SECURE_ENV), default=False)


def encrypt_secret(value: str) -> str:
    normalized = value or ""
    token = _get_fernet().encrypt(normalized.encode("utf-8")).decode("utf-8")
    return f"enc::{token}"


def decrypt_secret(value: str | None) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    if not normalized.startswith("enc::"):
        return normalized
    token = normalized.removeprefix("enc::")
    try:
        return _get_fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as error:
        raise RuntimeError("主密钥无效，无法解密运行时配置中的敏感字段") from error


def build_initial_admin_credentials() -> tuple[str, str, bool]:
    username = (os.environ.get(ADMIN_USERNAME_ENV) or "admin").strip() or "admin"
    env_password = (os.environ.get(ADMIN_PASSWORD_ENV) or "").strip()
    if env_password:
        return username, env_password, False
    generated_password = secrets.token_urlsafe(14)
    return username, generated_password, True


def emit_generated_admin_password(username: str, password: str) -> None:
    print(
        f"[wechat-md-server] Initial admin credentials generated. username={username} password={password}",
        flush=True,
    )


def check_login_allowed(identifier: str, now: float | None = None) -> tuple[bool, int | None]:
    current_time = now or time.time()
    with _LOGIN_LOCK:
        _prune_login_state(identifier, current_time)
        locked_until = _LOGIN_LOCKS.get(identifier)
        if locked_until and locked_until > current_time:
            return False, max(1, int(locked_until - current_time))
        return True, None


def record_login_failure(identifier: str, now: float | None = None) -> tuple[bool, int | None]:
    current_time = now or time.time()
    with _LOGIN_LOCK:
        attempts = _LOGIN_ATTEMPTS.setdefault(identifier, deque())
        attempts.append(current_time)
        _prune_login_state(identifier, current_time)
        if len(_LOGIN_ATTEMPTS.get(identifier, ())) > LOGIN_FAILURE_THRESHOLD:
            locked_until = current_time + LOGIN_LOCK_SECONDS
            _LOGIN_LOCKS[identifier] = locked_until
            return False, LOGIN_LOCK_SECONDS
        return True, None


def clear_login_failures(identifier: str) -> None:
    with _LOGIN_LOCK:
        _LOGIN_ATTEMPTS.pop(identifier, None)
        _LOGIN_LOCKS.pop(identifier, None)


def reset_login_rate_limit_state() -> None:
    with _LOGIN_LOCK:
        _LOGIN_ATTEMPTS.clear()
        _LOGIN_LOCKS.clear()


def _prune_login_state(identifier: str, current_time: float) -> None:
    attempts = _LOGIN_ATTEMPTS.get(identifier)
    if attempts is not None:
        while attempts and current_time - attempts[0] > LOGIN_FAILURE_WINDOW_SECONDS:
            attempts.popleft()
        if not attempts:
            _LOGIN_ATTEMPTS.pop(identifier, None)
    locked_until = _LOGIN_LOCKS.get(identifier)
    if locked_until and locked_until <= current_time:
        _LOGIN_LOCKS.pop(identifier, None)


def _get_fernet() -> Fernet:
    master_key = (os.environ.get(APP_MASTER_KEY_ENV) or "").strip()
    if not master_key:
        raise RuntimeError(f"{APP_MASTER_KEY_ENV} 未配置，无法加载运行时敏感配置")
    digest = hashlib.sha256(master_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
