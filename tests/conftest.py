from __future__ import annotations

import asyncio
import sys

import pytest


@pytest.fixture(scope="session")
def event_loop_policy():
    if sys.platform.startswith("win"):
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.DefaultEventLoopPolicy()
