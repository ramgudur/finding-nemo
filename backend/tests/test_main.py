from fastapi.testclient import TestClient

from app.agents.critic import CriticScheduler
from app.core.db import Db
from app.main import app, create_app


def test_health():
    r = TestClient(app).get("/v1/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_create_app_wires_a_critic_scheduler_by_default():
    a = create_app(db=Db(":memory:"), autostart_engine=False)
    assert isinstance(a.state.critic_scheduler, CriticScheduler)
    assert a.state.critic_scheduler.db is a.state.db
    assert a.state.critic_scheduler.engine is a.state.engine


def test_critic_scheduler_starts_and_stops_with_the_app():
    a = create_app(db=Db(":memory:"))
    with TestClient(a):
        assert a.state.critic_scheduler._task is not None
    assert a.state.critic_scheduler._task is None
