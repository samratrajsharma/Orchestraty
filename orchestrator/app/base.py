"""Declarative base for all ORM models.

Kept free of any engine so models can be imported without a live database
(useful for tests and for Alembic autogeneration).
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
