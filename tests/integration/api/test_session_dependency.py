from collections.abc import AsyncIterator, Callable
from typing import Annotated, Any, Self

import httpx
import pytest
from fastapi import Depends, FastAPI

from ai_greenhouse.api.dependencies import get_session
from ai_greenhouse.app import create_app
from ai_greenhouse.core.config import Settings

FAKE_DATABASE_URL = "postgresql+asyncpg://test_user:top-secret@test-db:5432/test"


class FakeSession:
    """Records the transaction boundary without opening a connection."""

    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        self.closed = True
        return False

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class FakeSessionFactory:
    def __init__(self) -> None:
        self.sessions: list[FakeSession] = []

    def __call__(self) -> FakeSession:
        session = FakeSession()
        self.sessions.append(session)
        return session


@pytest.fixture
def settings(build_settings: Callable[..., Settings]) -> Settings:
    return build_settings(FAKE_DATABASE_URL, app_env="test")


@pytest.fixture
def session_factory() -> FakeSessionFactory:
    return FakeSessionFactory()


@pytest.fixture
def app(settings: Settings, session_factory: FakeSessionFactory) -> FastAPI:
    application = create_app(settings)
    application.state.session_factory = session_factory

    @application.get("/probe/succeed")
    async def succeed(session: Annotated[Any, Depends(get_session)]) -> dict[str, bool]:
        return {"session": session is not None}

    @application.get("/probe/fail")
    async def fail(session: Annotated[Any, Depends(get_session)]) -> None:
        raise RuntimeError("handler failed")

    @application.get("/probe/twice")
    async def twice(
        first: Annotated[Any, Depends(get_session)],
        second: Annotated[Any, Depends(get_session)],
    ) -> dict[str, bool]:
        return {"same": first is second}

    return application


def client(app: FastAPI, *, raise_app_exceptions: bool = True) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=raise_app_exceptions),
        base_url="http://test",
    )


async def test_session_is_committed_when_the_handler_returns(
    app: FastAPI,
    session_factory: FakeSessionFactory,
) -> None:
    async with client(app) as http_client:
        response = await http_client.get("/probe/succeed")

    assert response.status_code == 200
    session = session_factory.sessions[0]
    assert session.commits == 1
    assert session.rollbacks == 0
    assert session.closed is True


async def test_session_is_rolled_back_when_the_handler_raises(
    app: FastAPI,
    session_factory: FakeSessionFactory,
) -> None:
    async with client(app, raise_app_exceptions=False) as http_client:
        response = await http_client.get("/probe/fail")

    assert response.status_code == 500
    session = session_factory.sessions[0]
    assert session.commits == 0
    assert session.rollbacks == 1
    assert session.closed is True


async def test_one_session_per_request(
    app: FastAPI,
    session_factory: FakeSessionFactory,
) -> None:
    async with client(app) as http_client:
        response = await http_client.get("/probe/twice")
        await http_client.get("/probe/succeed")

    assert response.json() == {"same": True}, "a request must share a single session"
    assert len(session_factory.sessions) == 2, "each request must open its own session"


async def test_session_dependency_can_be_overridden_in_tests(app: FastAPI) -> None:
    sentinel = FakeSession()

    async def override_session() -> AsyncIterator[FakeSession]:
        yield sentinel

    app.dependency_overrides[get_session] = override_session

    async with client(app) as http_client:
        response = await http_client.get("/probe/succeed")

    assert response.status_code == 200
    assert sentinel.commits == 0, "the override replaces the transaction boundary entirely"
