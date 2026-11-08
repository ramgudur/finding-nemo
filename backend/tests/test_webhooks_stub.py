import pytest
from fastapi.testclient import TestClient

from app.core.db import Db
from app.main import create_app


@pytest.mark.parametrize("method", ["get", "post", "put", "patch", "delete"])
def test_webhooks_is_a_501_stub(method):
    client = TestClient(create_app(db=Db(":memory:"), autostart_engine=False))
    r = getattr(client, method)("/v1/apps/any-app/webhooks")
    assert r.status_code == 501 and "not implemented" in r.json()["detail"]
