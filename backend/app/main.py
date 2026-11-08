"""FastAPI entry point. Routers and the engine/agent schedulers get mounted here as they land."""

from fastapi import FastAPI

app = FastAPI(title="Geo heat-map platform", version="0.1.0")


@app.get("/v1/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
