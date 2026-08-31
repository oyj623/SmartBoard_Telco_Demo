"""
Authentication.

bcrypt is called directly rather than through passlib: passlib 1.7.4 probes its
backend at import time with an over-length secret, which bcrypt 4.1+ rejects
outright, so `CryptContext(schemes=["bcrypt"])` raises on any machine with a
current bcrypt. The hashes are the same $2b$ format either way.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import List

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from .database import User, get_db

SECRET_KEY = os.getenv("JWT_SECRET", "nusatel-dev-secret-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 12 * 60

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

_BCRYPT_MAX = 72


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8")[:_BCRYPT_MAX], hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8")[:_BCRYPT_MAX], bcrypt.gensalt()).decode("utf-8")


def create_access_token(user_id: int, role: str, state_codes: List[str]) -> str:
    payload = {
        "sub": str(user_id),
        "role": role,
        "state_codes": state_codes,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    exc = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = int(payload["sub"])
    except (JWTError, KeyError, ValueError):
        raise exc

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise exc
    return user
