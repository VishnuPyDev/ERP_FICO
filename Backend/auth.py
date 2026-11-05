import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import smtplib
import ssl
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import get_db
from models import PasswordResetToken, User

router = APIRouter(prefix="/api/auth", tags=["Authentication"])


def _load_secret_key() -> str:
    configured_key = os.getenv("AUTH_SECRET_KEY", "").strip()
    if configured_key:
        if len(configured_key.encode("utf-8")) < 32:
            raise RuntimeError("AUTH_SECRET_KEY must contain at least 32 bytes. Set it in backend/.env.")
        return configured_key

    secret_file = Path(__file__).resolve().parents[1] / ".auth_secret"
    try:
        try:
            with secret_file.open("x", encoding="utf-8") as file:
                file.write(secrets.token_urlsafe(48) + "\n")
        except FileExistsError:
            pass
        secret_key = secret_file.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise RuntimeError(
            f"Unable to read or create the local authentication key at {secret_file}. "
            "Set AUTH_SECRET_KEY in backend/.env to a unique value of at least 32 bytes."
        ) from error

    if len(secret_key.encode("utf-8")) < 32:
        raise RuntimeError(
            f"The local authentication key at {secret_file} is invalid. "
            "Remove the invalid file or set AUTH_SECRET_KEY in backend/.env."
        )
    return secret_key


_secret_key = _load_secret_key()

_cookie_secure = os.getenv("AUTH_COOKIE_SECURE", "false").strip().lower() in {"1", "true", "yes"}
_cookie_name = "oneerp_session"
_session_lifetime_seconds = 12 * 60 * 60
_reset_token_lifetime = timedelta(minutes=30)
_password_iterations = 600_000
_email_pattern = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_logger = logging.getLogger(__name__)


class RegisterRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)
    full_name: str = Field(..., min_length=2, max_length=150)
    password: str = Field(..., min_length=12, max_length=256)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _email_pattern.fullmatch(normalized):
            raise ValueError("Enter a valid email address")
        return normalized

    @field_validator("full_name")
    @classmethod
    def normalize_full_name(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 2:
            raise ValueError("Full name must be at least 2 characters")
        return normalized


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)
    password: str = Field(..., min_length=1, max_length=256)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _email_pattern.fullmatch(normalized):
            raise ValueError("Enter a valid email address")
        return normalized


class EmailRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _email_pattern.fullmatch(normalized):
            raise ValueError("Enter a valid email address")
        return normalized


class ResetPasswordRequest(BaseModel):
    token: str = Field(..., min_length=32, max_length=256)
    password: str = Field(..., min_length=12, max_length=256)


class UserResponse(BaseModel):
    id: int
    email: str
    full_name: str

    @classmethod
    def from_user(cls, user: User) -> "UserResponse":
        return cls(id=user.id, email=user.email, full_name=user.full_name)


class AuthStatusResponse(BaseModel):
    authenticated: bool
    user: Optional[UserResponse] = None


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _create_session_token(user_id: int) -> str:
    now = int(time.time())
    header = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64url_encode(json.dumps({
        "sub": str(user_id),
        "iat": now,
        "exp": now + _session_lifetime_seconds,
    }, separators=(",", ":")).encode())
    signing_input = f"{header}.{payload}".encode("ascii")
    signature = hmac.new(_secret_key.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64url_encode(signature)}"


def _read_session_user_id(token: str) -> Optional[int]:
    try:
        header_part, payload_part, signature_part = token.split(".")
        header_bytes = base64.urlsafe_b64decode(header_part + "=" * (-len(header_part) % 4))
        header = json.loads(header_bytes)
        if not isinstance(header, dict) or header.get("alg") != "HS256" or header.get("typ") != "JWT":
            return None

        signing_input = f"{header_part}.{payload_part}".encode("ascii")
        signature = base64.urlsafe_b64decode(signature_part + "=" * (-len(signature_part) % 4))
        expected_signature = hmac.new(_secret_key.encode("utf-8"), signing_input, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected_signature):
            return None

        payload_bytes = base64.urlsafe_b64decode(payload_part + "=" * (-len(payload_part) % 4))
        payload = json.loads(payload_bytes)
        user_id = int(payload["sub"])
        if int(payload["exp"]) <= int(time.time()):
            return None
        return user_id
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _password_iterations)
    return f"pbkdf2_sha256${_password_iterations}${_b64url_encode(salt)}${_b64url_encode(digest)}"


def _verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations, salt_part, digest_part = stored_hash.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_part + "=" * (-len(salt_part) % 4))
        expected_digest = base64.urlsafe_b64decode(digest_part + "=" * (-len(digest_part) % 4))
        actual_digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
        return hmac.compare_digest(actual_digest, expected_digest)
    except (ValueError, TypeError):
        return False


def _password_reset_email_settings() -> tuple[str, int, str, str, str, bool, bool]:
    host = os.getenv("SMTP_HOST", "").strip()
    sender = os.getenv("SMTP_FROM_EMAIL", "").strip()
    if not host or not sender:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Password recovery email is not configured. Contact the administrator.",
        )

    try:
        port = int(os.getenv("SMTP_PORT", "587"))
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Password recovery email is not configured correctly. Contact the administrator.",
        ) from error
    if not 1 <= port <= 65535:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Password recovery email is not configured correctly. Contact the administrator.",
        )

    username = os.getenv("SMTP_USERNAME", "").strip()
    password = os.getenv("SMTP_PASSWORD", "")
    use_ssl = os.getenv("SMTP_USE_SSL", "false").strip().lower() in {"1", "true", "yes"}
    use_starttls = os.getenv("SMTP_USE_STARTTLS", "true").strip().lower() in {"1", "true", "yes"}
    if (username and not password) or (use_ssl and use_starttls):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Password recovery email is not configured correctly. Contact the administrator.",
        )
    return host, port, username, password, sender, use_ssl, use_starttls


def _send_password_reset_email(
    recipient: str,
    reset_url: str,
    settings: tuple[str, int, str, str, str, bool, bool],
) -> None:
    host, port, username, password, sender, use_ssl, use_starttls = settings
    message = EmailMessage()
    message["Subject"] = "Reset your OneERP password"
    message["From"] = sender
    message["To"] = recipient
    message.set_content(
        "We received a request to reset the password for your OneERP account.\n\n"
        f"Use this link within 30 minutes to choose a new password:\n{reset_url}\n\n"
        "If you did not request a password reset, you can ignore this email."
    )

    context = ssl.create_default_context()
    if use_ssl:
        smtp_connection = smtplib.SMTP_SSL(host, port, timeout=15, context=context)
    else:
        smtp_connection = smtplib.SMTP(host, port, timeout=15)
    with smtp_connection as smtp:
        if use_starttls:
            smtp.starttls(context=context)
        if username:
            smtp.login(username, password)
        smtp.send_message(message)


def _set_session_cookie(response: Response, user_id: int) -> None:
    response.set_cookie(
        key=_cookie_name,
        value=_create_session_token(user_id),
        max_age=_session_lifetime_seconds,
        httponly=True,
        secure=_cookie_secure,
        samesite="strict",
        path="/",
    )


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    token = request.cookies.get(_cookie_name)
    user_id = _read_session_user_id(token) if token else None
    user = db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first() if user_id else None
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in to continue",
        )
    return user


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(
    user_in: RegisterRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> UserResponse:
    if db.query(User).filter(User.email == user_in.email).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An account with this email already exists")

    user = User(
        email=user_in.email,
        full_name=user_in.full_name,
        password_hash=_hash_password(user_in.password),
        is_active=True,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An account with this email already exists")
    db.refresh(user)
    _set_session_cookie(response, user.id)
    return UserResponse.from_user(user)


@router.post("/forgot-password", status_code=status.HTTP_202_ACCEPTED)
def request_password_reset(
    request_in: EmailRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    email_settings = _password_reset_email_settings()
    user = db.query(User).filter(User.email == request_in.email, User.is_active.is_(True)).first()
    if user is not None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        recent_request = db.query(PasswordResetToken).filter(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.created_at >= now - timedelta(seconds=60),
        ).first()
        if recent_request is not None:
            return {
                "message": "If an account exists for that email, password reset instructions will be sent."
            }

        raw_token = secrets.token_urlsafe(32)
        reset_token = PasswordResetToken(
            user_id=user.id,
            token_hash=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
            expires_at=now + _reset_token_lifetime,
        )

        db.add(reset_token)
        db.commit()

        public_url = os.getenv("APP_PUBLIC_URL", "").strip().rstrip("/")
        if not public_url:
            public_url = str(request.base_url).rstrip("/")
        reset_url = f"{public_url}/#{urlencode({'reset_token': raw_token})}"
        try:
            _send_password_reset_email(user.email, reset_url, email_settings)
        except (smtplib.SMTPException, OSError, ssl.SSLError):
            _logger.exception("Could not send password reset email")
            db.delete(reset_token)
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Password recovery email could not be sent. Contact the administrator.",
            )

    return {
        "message": "If an account exists for that email, password reset instructions will be sent."
    }


@router.post("/reset-password")
def reset_password(
    reset_in: ResetPasswordRequest,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    token_hash = hashlib.sha256(reset_in.token.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    reset_token = db.query(PasswordResetToken).filter(
        PasswordResetToken.token_hash == token_hash,
        PasswordResetToken.used_at.is_(None),
        PasswordResetToken.expires_at > now,
    ).first()
    if reset_token is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This password reset link is invalid or has expired. Request a new one.",
        )

    user = db.query(User).filter(User.id == reset_token.user_id, User.is_active.is_(True)).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This password reset link is invalid or has expired. Request a new one.",
        )

    user.password_hash = _hash_password(reset_in.password)
    reset_token.used_at = now
    db.query(PasswordResetToken).filter(
        PasswordResetToken.user_id == user.id,
        PasswordResetToken.id != reset_token.id,
        PasswordResetToken.used_at.is_(None),
    ).update({"used_at": now}, synchronize_session=False)
    db.commit()
    return {"message": "Your password has been updated. You can now sign in."}


@router.post("/login", response_model=UserResponse)
def login(
    credentials: LoginRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> UserResponse:
    user = db.query(User).filter(User.email == credentials.email).first()
    if user is None or not user.is_active or not _verify_password(credentials.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Email or password is incorrect")

    _set_session_cookie(response, user.id)
    return UserResponse.from_user(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response) -> None:
    response.delete_cookie(
        key=_cookie_name,
        httponly=True,
        secure=_cookie_secure,
        samesite="strict",
        path="/",
    )


@router.get("/me", response_model=AuthStatusResponse)
def get_session(
    request: Request,
    db: Session = Depends(get_db),
) -> AuthStatusResponse:
    token = request.cookies.get(_cookie_name)
    user_id = _read_session_user_id(token) if token else None
    user = db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first() if user_id else None
    if user is None:
        return AuthStatusResponse(authenticated=False)
    return AuthStatusResponse(authenticated=True, user=UserResponse.from_user(user))
