"""Password hashing: argon2id only. Passwords never logged, never in URLs."""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher(time_cost=3, memory_cost=64 * 1024, parallelism=2)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(hashed: str) -> bool:
    try:
        return _hasher.check_needs_rehash(hashed)
    except InvalidHashError:
        return True


def validate_password_strength(password: str) -> list[str]:
    """Baseline composition policy; org-level policy refinement later."""
    problems: list[str] = []
    if len(password) < 12:
        problems.append("Password must be at least 12 characters.")
    if not any(c.isupper() for c in password):
        problems.append("Password must contain an uppercase letter.")
    if not any(c.islower() for c in password):
        problems.append("Password must contain a lowercase letter.")
    if not any(c.isdigit() for c in password):
        problems.append("Password must contain a digit.")
    return problems
