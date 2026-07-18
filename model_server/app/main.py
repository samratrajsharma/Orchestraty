"""Orchestraty model server.

A live inference service - the locally-runnable analog of a Kubernetes model
deployment. Holds two slots (stable + canary), loads MLflow models by URI, and
routes /predict traffic by canary weight. The deploy agent drives it (load ->
canary -> promote/rollback).
"""

import logging
import os
import random

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .routing import pick_slot

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("model-server")

TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "http://mlflow:5000")


class _Slot:
    def __init__(self):
        self.model = None
        self.model_uri = None


STATE = {"stable": _Slot(), "canary": _Slot(), "canary_weight": 0}

app = FastAPI(title="Orchestraty Model Server", version="0.1.0")


class LoadRequest(BaseModel):
    model_uri: str
    slot: str = "stable"
    weight: int | None = None


class PredictRequest(BaseModel):
    inputs: list


def _load_model(uri: str):
    import mlflow
    import mlflow.pyfunc

    mlflow.set_tracking_uri(TRACKING_URI)
    return mlflow.pyfunc.load_model(uri)


def _status() -> dict:
    return {
        "stable": STATE["stable"].model_uri,
        "canary": STATE["canary"].model_uri,
        "canary_weight": STATE["canary_weight"],
    }


@app.get("/health", tags=["system"])
def health() -> dict:
    return {"status": "healthy", "service": "model-server"}


@app.get("/status", tags=["serving"])
def status() -> dict:
    return _status()


@app.post("/load", tags=["serving"])
def load(req: LoadRequest) -> dict:
    if req.slot not in ("stable", "canary"):
        raise HTTPException(status_code=400, detail="slot must be 'stable' or 'canary'")
    try:
        model = _load_model(req.model_uri)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"failed to load model: {exc}")
    STATE[req.slot].model = model
    STATE[req.slot].model_uri = req.model_uri
    if req.slot == "canary":
        STATE["canary_weight"] = req.weight if req.weight is not None else 10
    log.info("loaded %s into %s slot", req.model_uri, req.slot)
    return _status()


@app.post("/promote", tags=["serving"])
def promote() -> dict:
    if STATE["canary"].model is None:
        raise HTTPException(status_code=400, detail="no canary to promote")
    STATE["stable"].model = STATE["canary"].model
    STATE["stable"].model_uri = STATE["canary"].model_uri
    STATE["canary"] = _Slot()
    STATE["canary_weight"] = 0
    log.info("promoted canary -> stable")
    return _status()


@app.post("/rollback", tags=["serving"])
def rollback() -> dict:
    STATE["canary"] = _Slot()
    STATE["canary_weight"] = 0
    log.info("rolled back canary")
    return _status()


@app.post("/predict", tags=["serving"])
def predict(req: PredictRequest) -> dict:
    slot = pick_slot(STATE["canary"].model is not None, STATE["canary_weight"], random.random())
    model = STATE[slot].model
    if model is None:
        slot = "stable"
        model = STATE["stable"].model
    if model is None:
        raise HTTPException(status_code=409, detail="no model loaded")
    import numpy as np

    preds = model.predict(np.array(req.inputs))
    out = preds.tolist() if hasattr(preds, "tolist") else list(preds)
    return {"served_by": slot, "model_uri": STATE[slot].model_uri, "predictions": out}
