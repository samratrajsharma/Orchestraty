#!/usr/bin/env python3
"""
Orchestraty — Management CLI

Admin commands for managing users, roles, and system operations.

Usage (from inside the api container):
    python manage.py promote-admin user@example.com
    python manage.py list-users
    python manage.py create-superuser
    python manage.py deactivate-user user@example.com
    python manage.py activate-user user@example.com
    python manage.py set-role user@example.com reviewer
    python manage.py reset-password user@example.com
"""

import argparse
import getpass
import sys
from pathlib import Path

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).parent))

from sqlalchemy import select, update as sql_update
from core.database_sync import SyncSessionLocal
from core.models.user import User
from core.services.auth_service import hash_password


VALID_ROLES = ("admin", "reviewer", "annotator", "viewer")


def _get_user(db, email: str) -> User | None:
    result = db.execute(select(User).where(User.email == email))
    return result.scalars().first()


def cmd_list_users(args):
    """List all users with their roles and status."""
    db = SyncSessionLocal()
    try:
        result = db.execute(select(User).order_by(User.created_at))
        users = result.scalars().all()

        if not users:
            print("No users found.")
            return

        print(f"\n{'Email':<35} {'Name':<25} {'Role':<12} {'Active':<8} {'Created'}")
        print("─" * 110)
        for u in users:
            active = "✓" if u.is_active else "✗"
            created = u.created_at.strftime("%Y-%m-%d %H:%M") if u.created_at else "—"
            print(f"{u.email:<35} {(u.full_name or '—'):<25} {u.role:<12} {active:<8} {created}")
        print(f"\nTotal: {len(users)} users")
    finally:
        db.close()


def cmd_promote_admin(args):
    """Promote a user to admin role."""
    db = SyncSessionLocal()
    try:
        user = _get_user(db, args.email)
        if not user:
            print(f"Error: User '{args.email}' not found.")
            sys.exit(1)

        if user.role == "admin":
            print(f"User '{args.email}' is already an admin.")
            return

        old_role = user.role
        db.execute(
            sql_update(User).where(User.email == args.email).values(role="admin")
        )
        db.commit()
        print(f"Promoted '{args.email}' from '{old_role}' to 'admin'.")
    finally:
        db.close()


def cmd_set_role(args):
    """Set a user's role."""
    if args.role not in VALID_ROLES:
        print(f"Error: Invalid role '{args.role}'. Valid roles: {', '.join(VALID_ROLES)}")
        sys.exit(1)

    db = SyncSessionLocal()
    try:
        user = _get_user(db, args.email)
        if not user:
            print(f"Error: User '{args.email}' not found.")
            sys.exit(1)

        old_role = user.role
        db.execute(
            sql_update(User).where(User.email == args.email).values(role=args.role)
        )
        db.commit()
        print(f"Changed '{args.email}' role: '{old_role}' → '{args.role}'.")
    finally:
        db.close()


def cmd_deactivate_user(args):
    """Deactivate a user account."""
    db = SyncSessionLocal()
    try:
        user = _get_user(db, args.email)
        if not user:
            print(f"Error: User '{args.email}' not found.")
            sys.exit(1)

        if not user.is_active:
            print(f"User '{args.email}' is already deactivated.")
            return

        db.execute(
            sql_update(User).where(User.email == args.email).values(is_active=False)
        )
        db.commit()
        print(f"Deactivated user '{args.email}'.")
    finally:
        db.close()


def cmd_activate_user(args):
    """Activate a user account."""
    db = SyncSessionLocal()
    try:
        user = _get_user(db, args.email)
        if not user:
            print(f"Error: User '{args.email}' not found.")
            sys.exit(1)

        if user.is_active:
            print(f"User '{args.email}' is already active.")
            return

        db.execute(
            sql_update(User).where(User.email == args.email).values(is_active=True)
        )
        db.commit()
        print(f"Activated user '{args.email}'.")
    finally:
        db.close()


def cmd_create_superuser(args):
    """Create a new admin user interactively."""
    email = input("Email: ").strip()
    if not email:
        print("Error: Email cannot be empty.")
        sys.exit(1)

    full_name = input("Full name: ").strip()
    password = getpass.getpass("Password: ")
    confirm = getpass.getpass("Confirm password: ")

    if password != confirm:
        print("Error: Passwords do not match.")
        sys.exit(1)

    if len(password) < 8:
        print("Error: Password must be at least 8 characters.")
        sys.exit(1)

    db = SyncSessionLocal()
    try:
        existing = _get_user(db, email)
        if existing:
            print(f"Error: User '{email}' already exists.")
            sys.exit(1)

        user = User(
            email=email,
            hashed_password=hash_password(password),
            full_name=full_name,
            role="admin",
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        print(f"\nSuperuser created:")
        print(f"  Email: {email}")
        print(f"  Name:  {full_name}")
        print(f"  Role:  admin")
        print(f"  ID:    {user.id}")
    finally:
        db.close()


def cmd_reset_password(args):
    """Reset a user's password."""
    db = SyncSessionLocal()
    try:
        user = _get_user(db, args.email)
        if not user:
            print(f"Error: User '{args.email}' not found.")
            sys.exit(1)

        password = getpass.getpass("New password: ")
        confirm = getpass.getpass("Confirm password: ")

        if password != confirm:
            print("Error: Passwords do not match.")
            sys.exit(1)

        if len(password) < 8:
            print("Error: Password must be at least 8 characters.")
            sys.exit(1)

        db.execute(
            sql_update(User)
            .where(User.email == args.email)
            .values(hashed_password=hash_password(password))
        )
        db.commit()
        print(f"Password reset for '{args.email}'.")
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(
        prog="manage.py",
        description="Orchestraty Management CLI",
    )
    sub = parser.add_subparsers(dest="command", help="Available commands")

    # list-users
    sub.add_parser("list-users", help="List all users")

    # promote-admin
    p = sub.add_parser("promote-admin", help="Promote user to admin")
    p.add_argument("email", help="User email address")

    # set-role
    p = sub.add_parser("set-role", help="Set user role")
    p.add_argument("email", help="User email address")
    p.add_argument("role", choices=VALID_ROLES, help="New role")

    # deactivate-user
    p = sub.add_parser("deactivate-user", help="Deactivate a user")
    p.add_argument("email", help="User email address")

    # activate-user
    p = sub.add_parser("activate-user", help="Activate a user")
    p.add_argument("email", help="User email address")

    # create-superuser
    sub.add_parser("create-superuser", help="Create a new admin user")

    # reset-password
    p = sub.add_parser("reset-password", help="Reset user password")
    p.add_argument("email", help="User email address")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    commands = {
        "list-users": cmd_list_users,
        "promote-admin": cmd_promote_admin,
        "set-role": cmd_set_role,
        "deactivate-user": cmd_deactivate_user,
        "activate-user": cmd_activate_user,
        "create-superuser": cmd_create_superuser,
        "reset-password": cmd_reset_password,
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()
