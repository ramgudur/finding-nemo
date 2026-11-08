"""Every hand-written spec in specs/ must validate against the AppSpec contract."""

import json
from pathlib import Path

import pytest

from app.core.models import AppSpec
from app.engine.operators import registry

SPECS = sorted((Path(__file__).resolve().parents[2] / "specs").glob("*.json"))


@pytest.mark.parametrize("path", SPECS, ids=lambda p: p.name)
def test_spec_validates(path):
    spec = AppSpec.model_validate(json.loads(path.read_text(encoding="utf-8")))
    assert spec.status == "draft"  # hand-written specs still go through dev confirmation


@pytest.mark.parametrize("path", SPECS, ids=lambda p: p.name)
def test_registered_operator_params_parse(path):
    spec = AppSpec.model_validate(json.loads(path.read_text(encoding="utf-8")))
    for inp in spec.inputs:
        for o in inp.operators:
            if o.op in registry.OPERATORS:  # operators not built yet are checked once they register
                registry.parse_params(o.op, o.params())
