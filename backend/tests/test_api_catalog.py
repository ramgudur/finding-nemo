from fastapi.testclient import TestClient

from app.core.db import Db
from app.engine.operators import registry
from app.main import create_app


def test_operator_catalogue_is_public_and_matches_the_registry():
    with TestClient(create_app(db=Db(":memory:"), autostart_engine=False)) as client:
        r = client.get("/v1/operators")
    assert r.status_code == 200
    body = r.json()
    assert [o["name"] for o in body] == [o["name"] for o in registry.list_operators()]
    for o in body:
        assert o["description"] and o["shapes"] and o["role"] in ("layer", "override")
        assert o["params"].get("type") == "object"
