
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import declarative_base, relationship

# Shared SQLAlchemy base for authentication models
Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)

    email = Column(
        String(254),
        nullable=False,
        unique=True,
        index=True,
    )

    full_name = Column(
        String(150),
        nullable=False,
    )

    password_hash = Column(
        String(255),
        nullable=False,
    )

    # New users receive the least-privileged role.
    role = Column(
        String(30),
        nullable=False,
        default="employee",
        server_default="employee",
    )

    is_active = Column(
        Boolean,
        nullable=False,
        default=True,
        server_default="1",
    )

    created_at = Column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    updated_at = Column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    password_reset_tokens = relationship(
        "PasswordResetToken",
        back_populates="user",
        cascade="all, delete-orphan",
    )


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id = Column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    token_hash = Column(
        String(64),
        nullable=False,
        unique=True,
    )

    created_at = Column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    expires_at = Column(
        DateTime,
        nullable=False,
    )

    used_at = Column(
        DateTime,
        nullable=True,
    )

    user = relationship(
        "User",
        back_populates="password_reset_tokens",
    )

    __table_args__ = (
        Index("ix_password_reset_user_id", "user_id"),
        Index("ix_password_reset_expiry", "expires_at"),
    )
