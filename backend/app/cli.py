"""Operational commands.

python -m app.cli generate-master-key
python -m app.cli create-admin --username admin@congty.vn --name "Quản trị"
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from sqlalchemy import select


def _generate_key() -> None:
    from app.core.secrets.crypto import Keyring

    print(f"v1:{Keyring.generate_key()}")


async def _create_admin(username: str, name: str) -> int:
    from app.core.auth.models import User
    from app.core.auth.passwords import hash_password, password_problems
    from app.core.config import get_settings
    from app.core.core_manifest import CORE_MANIFEST
    from app.core.db import dispose_engine, get_sessionmaker
    from app.core.modules.registry import build_registry
    from app.core.rbac.service import grant_role, sync_permissions

    settings = get_settings()
    password = getpass.getpass("Mật khẩu: ")
    problems = password_problems(password, settings.local_min_password_length)
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    if getpass.getpass("Nhập lại mật khẩu: ") != password:
        print("Mật khẩu không khớp.", file=sys.stderr)
        return 1
    async with get_sessionmaker()() as session:
        await sync_permissions(session, build_registry(CORE_MANIFEST, settings.enabled_modules))
        user = await session.scalar(select(User).where(User.upn == username.lower()))
        if user is None:
            user = User(upn=username.lower(), display_name=name)
            session.add(user)
        user.password_hash = hash_password(password)
        user.is_active = True
        await session.flush()
        await grant_role(session, user.id, "admin")
        await session.commit()
    await dispose_engine()
    print(f"Đã tạo/cập nhật quản trị viên {username}.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("generate-master-key")
    admin = sub.add_parser("create-admin")
    admin.add_argument("--username", required=True)
    admin.add_argument("--name", default="Quản trị viên")
    args = parser.parse_args()
    if args.cmd == "generate-master-key":
        _generate_key()
    elif args.cmd == "create-admin":
        sys.exit(asyncio.run(_create_admin(args.username, args.name)))


if __name__ == "__main__":
    main()
