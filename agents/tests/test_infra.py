import pytest

from infra_agent import plan


def test_default_and_k8s():
    p = plan.build_plan({})
    assert p["provider"] == "local" and p["resource"] == "k8s_namespace"
    assert "kubernetes_namespace" in plan.to_terraform(p)


def test_gpu_pool_and_bucket():
    hcl = plan.to_terraform(plan.build_plan({"provider": "gcp", "resource": "gpu_node_pool",
                                             "name": "tp", "size": {"nodes": 3, "machine_type": "a2"}}))
    assert "node_count = 3" in hcl and "a2" in hcl
    assert "aws_s3_bucket" in plan.to_terraform(plan.build_plan(
        {"provider": "aws", "resource": "bucket", "name": "b"}))


def test_errors():
    for bad in [{"provider": "azure"}, {"resource": "quantum"}, {"size": "big"}]:
        with pytest.raises(plan.PlanError):
            plan.build_plan(bad)
