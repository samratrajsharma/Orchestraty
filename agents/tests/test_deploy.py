import pytest

from deploy_agent import manifests, rollout


def test_rollout_plans():
    assert rollout.plan("direct") == [{"op": "load", "slot": "stable"}]
    assert rollout.plan("blue-green") == [{"op": "load", "slot": "stable"}]
    assert rollout.plan("canary", 25) == [{"op": "load", "slot": "canary", "weight": 25}]
    assert rollout.plan("canary", 25, True) == [
        {"op": "load", "slot": "canary", "weight": 25}, {"op": "promote"}]


def test_rollout_error():
    with pytest.raises(rollout.RolloutError):
        rollout.plan("yolo")


def test_manifests():
    assert "models:/x/latest" in manifests.dockerfile("x")
    k = manifests.k8s("x", replicas=3)
    assert "kind: Deployment" in k and "kind: Service" in k and "replicas: 3" in k
