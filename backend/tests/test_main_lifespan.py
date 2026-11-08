"""main.py lifespan: the tick engine, Critic, POI Labeler, connector engine and Scout runner start on startup and
stop on shutdown. Fakes only; no network, no LLM."""

from fastapi.testclient import TestClient

from app.core.db import Db
from app.main import create_app


class FakeLoop:
    def __init__(self, name: str, log: list[str], fail_stop: bool = False) -> None:
        self.name, self.log, self.fail_stop = name, log, fail_stop

    def start(self) -> None:
        self.log.append(f"start {self.name}")

    async def stop(self) -> None:
        self.log.append(f"stop {self.name}")
        if self.fail_stop:
            raise RuntimeError("stop failed")


class FakeEngine(FakeLoop):
    def __init__(self, log: list[str]) -> None:
        super().__init__("engine", log)
        self.apps: dict = {}

    def add_app(self, spec) -> None:
        self.apps[spec.app_id] = spec


class FakeLlm:
    def log_startup_models(self, agents) -> None:
        pass


class FakeCritic(FakeLoop):
    def __init__(self, log: list[str]) -> None:
        super().__init__("critic", log)
        self.llm = FakeLlm()


def build(log, **over):
    parts = {
        "engine": FakeEngine(log),
        "critic_scheduler": FakeCritic(log),
        "poi_labeler_scheduler": FakeLoop("poi_labeler", log),
        "connector_engine": FakeLoop("connectors", log),
        "scout_runner": FakeLoop("scout", log),
    }
    parts.update(over)
    return create_app(db=Db(":memory:"), autostart_engine=True, **parts), parts


def test_startup_and_shutdown_run_all_five_loops_in_order():
    log: list[str] = []
    app, parts = build(log)
    with TestClient(app):
        assert log == ["start engine", "start critic", "start poi_labeler", "start connectors", "start scout"]
    assert log[5:] == ["stop scout", "stop connectors", "stop poi_labeler", "stop critic", "stop engine"]
    assert app.state.scout_runner is parts["scout_runner"]
    assert app.state.poi_labeler_scheduler is parts["poi_labeler_scheduler"]
    assert app.state.connector_engine is parts["connector_engine"]


def test_one_failing_stop_does_not_leave_others_running():
    log: list[str] = []
    app, _ = build(log, poi_labeler_scheduler=FakeLoop("poi_labeler", log, fail_stop=True))
    with TestClient(app):
        pass
    assert log[5:] == ["stop scout", "stop connectors", "stop poi_labeler", "stop critic", "stop engine"]


def test_nothing_starts_without_autostart():
    log: list[str] = []
    parts = {
        "engine": FakeEngine(log),
        "critic_scheduler": FakeCritic(log),
        "poi_labeler_scheduler": FakeLoop("poi_labeler", log),
        "connector_engine": FakeLoop("connectors", log),
        "scout_runner": FakeLoop("scout", log),
    }
    with TestClient(create_app(db=Db(":memory:"), autostart_engine=False, **parts)):
        pass
    assert log == []


def test_default_poi_labeler_shares_the_critics_llm_router():
    """One LlmRouter means one set of per-provider token buckets: the shared rate budget."""
    log: list[str] = []
    critic = FakeCritic(log)
    app = create_app(
        db=Db(":memory:"),
        engine=FakeEngine(log),
        critic_scheduler=critic,
        connector_engine=FakeLoop("connectors", log),
        autostart_engine=False,
    )
    assert app.state.poi_labeler_scheduler.llm is critic.llm


def test_default_scout_and_connector_locator_share_the_router():
    """The Scout runner and the connector engine's article locator use the same LlmRouter as the Critic."""
    log: list[str] = []
    critic = FakeCritic(log)
    app = create_app(db=Db(":memory:"), engine=FakeEngine(log), critic_scheduler=critic, autostart_engine=False)
    assert app.state.scout_runner.llm is critic.llm
    assert app.state.connector_engine.locate is not None  # RSS items without coordinates get located
