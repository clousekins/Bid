from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db import session as sess
from app.db.models import AtsBoard, utcnow
from app.db.session import get_session_factory, init_db
from app.services.ats_catalog import mark_board, next_unchecked_boards, seed_ats_catalog


SEED = [
    ("greenhouse", "acme"),
    ("greenhouse", "acme"),
    ("lever", "beta"),
    ("ashby", "gamma"),
]


@pytest.mark.asyncio
async def test_seed_unique_and_pending_first(tmp_path, monkeypatch):
    db_file = tmp_path / "ats.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    monkeypatch.setenv("ACCESS_TOKEN", "")
    monkeypatch.setenv("PERSONA_OUTPUT_ROOT", str(tmp_path / "applications"))
    monkeypatch.setattr("app.services.ats_catalog._load_seed", lambda: list(SEED))
    get_settings.cache_clear()
    sess._engine = None
    sess._session_factory = None
    await init_db()

    factory = get_session_factory()
    async with factory() as session:
        rows = (await session.execute(select(AtsBoard).order_by(AtsBoard.id))).scalars().all()
        assert [(r.source, r.token) for r in rows] == [
            ("greenhouse", "acme"),
            ("lever", "beta"),
            ("ashby", "gamma"),
        ]
        assert all(r.status == "pending" and r.last_checked_at is None for r in rows)

        added = await seed_ats_catalog(session)
        assert added == 0

        acme = rows[0]
        await mark_board(session, acme, jobs_found=4, company_name="Acme")
        await session.commit()

        nxt = await next_unchecked_boards(session, limit=10)
        tokens = [b.token for b in nxt]
        assert "acme" not in tokens
        assert tokens == ["beta", "gamma"]

        acme.last_checked_at = utcnow() - timedelta(days=20)
        await session.commit()
        nxt2 = await next_unchecked_boards(session, limit=10)
        assert [b.token for b in nxt2][:1] == ["beta"]
        assert "acme" in {b.token for b in nxt2}
