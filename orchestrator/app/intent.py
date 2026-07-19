"""Intent compiler (pure): validate a workflow spec and resolve task keys to ids."""

import uuid

from .dag import validate_dag

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_TIMEOUT = 300


class CompileError(ValueError):
    pass


def compile_spec(spec: dict) -> dict:
    name = spec.get("name")
    if not name:
        raise CompileError("workflow spec requires a name")
    tasks = spec.get("tasks") or []
    if not tasks:
        raise CompileError("workflow spec requires at least one task")

    keys = [t.get("key") for t in tasks]
    if any(not k for k in keys):
        raise CompileError("every task requires a key")
    if len(set(keys)) != len(keys):
        raise CompileError("task keys must be unique")

    key_set = set(keys)
    for t in tasks:
        if not t.get("capability"):
            raise CompileError(f"task '{t.get('key')}' requires a capability")
        for dep in t.get("depends_on", []) or []:
            if dep not in key_set:
                raise CompileError(f"task '{t['key']}' depends on unknown key '{dep}'")

    try:
        validate_dag(
            [
                {"id": t["key"], "depends_on": list(t.get("depends_on", []) or []), "status": "QUEUED"}
                for t in tasks
            ]
        )
    except ValueError as exc:
        raise CompileError(f"invalid task graph: {exc}") from exc

    id_map = {k: str(uuid.uuid4()) for k in keys}
    compiled = []
    for t in tasks:
        compiled.append(
            {
                "id": id_map[t["key"]],
                "name": t.get("name") or t["key"],
                "capability": t["capability"],
                "payload": t.get("payload") or {},
                "depends_on": [id_map[d] for d in (t.get("depends_on", []) or [])],
                "max_attempts": int(t.get("max_attempts", DEFAULT_MAX_ATTEMPTS)),
                "timeout_seconds": int(t.get("timeout_seconds", DEFAULT_TIMEOUT)),
            }
        )
    return {"name": name, "intent": spec.get("intent"), "spec": spec, "tasks": compiled}


TEMPLATES = {
    "echo-demo": {
        "name": "Echo demo",
        "intent": "Run a two-step echo workflow",
        "tasks": [
            {"key": "hello", "name": "say hello", "capability": "echo", "payload": {"message": "hello"}},
            {"key": "world", "name": "say world", "capability": "echo",
             "payload": {"message": "world"}, "depends_on": ["hello"]},
        ],
    },
    "summarize": {
        "name": "Summarize text",
        "intent": "Summarize a piece of text with the LLMOps agent",
        "tasks": [
            {"key": "sum", "name": "summarize", "capability": "llm.complete",
             "payload": {"template": "summarize", "complexity": "low",
                         "vars": {"text": "Orchestraty is an agent-driven AI operations platform "
                                          "that manages models, LLMs, and infrastructure the way "
                                          "Kubernetes manages containers."}}},
        ],
    },
    "extract-contact": {
        "name": "Extract contact fields",
        "intent": "Pull structured fields from unstructured text",
        "tasks": [
            {"key": "ex", "name": "extract", "capability": "data.transform",
             "payload": {"text": "Reach us at hello@orchestraty.ai or +1 415 555 0142, docs at https://orchestraty.ai",
                         "schema": [{"name": "email", "type": "email"},
                                    {"name": "phone", "type": "phone"},
                                    {"name": "url", "type": "url"}]}},
        ],
    },
    "ml-pipeline": {
        "name": "ML pipeline (multi-agent)",
        "intent": "Extract data hints, train a model, then explain the run - across three agents",
        "tasks": [
            {"key": "extract", "name": "extract features", "capability": "data.transform",
             "payload": {"text": "Train on the iris dataset, snapshot 2026-07-18.",
                         "schema": [{"name": "dataset", "type": "regex", "pattern": "iris|wine"},
                                    {"name": "date", "type": "date"}]}},
            {"key": "train", "name": "train model", "capability": "mlops.train",
             "payload": {"dataset": "iris", "model": "random_forest",
                         "registered_model_name": "iris-pipeline"},
             "depends_on": ["extract"]},
            {"key": "explain", "name": "explain results", "capability": "llm.complete",
             "payload": {"template": "summarize", "complexity": "low",
                         "vars": {"text": "A random forest was trained on the iris dataset and registered."}},
             "depends_on": ["train"]},
        ],
    },
    "provision-cluster": {
        "name": "Provision GPU cluster",
        "intent": "Provision a GPU node pool via the infrastructure agent",
        "tasks": [
            {"key": "infra", "name": "provision", "capability": "infra.provision",
             "payload": {"provider": "gcp", "resource": "gpu_node_pool", "name": "train-pool",
                         "size": {"nodes": 3, "machine_type": "a2-highgpu-1g"}}},
        ],
    },
    "train-and-serve": {
        "name": "Train and serve",
        "intent": "Train a model, register it, then deploy it to the model server - end to end",
        "tasks": [
            {"key": "train", "name": "train model", "capability": "mlops.train",
             "payload": {"dataset": "iris", "model": "random_forest",
                         "registered_model_name": "iris-classifier"}},
            {"key": "serve", "name": "deploy model", "capability": "deploy.model",
             "payload": {"model_uri": "models:/iris-classifier/latest",
                         "name": "iris-classifier", "strategy": "direct"},
             "depends_on": ["train"]},
        ],
    },
    "train-and-deploy": {
        "name": "Train and deploy (legacy placeholder)",
        "intent": "Prepare data, train a model, then deploy it",
        "tasks": [
            {"key": "prep", "name": "prepare data", "capability": "data.prep", "payload": {}},
            {"key": "train", "name": "train model", "capability": "mlops.train",
             "payload": {}, "depends_on": ["prep"]},
            {"key": "deploy", "name": "deploy model", "capability": "deploy.k8s",
             "payload": {}, "depends_on": ["train"]},
        ],
    },
}


TEMPLATES["heal-demo"] = {
    "name": "Heal on breach",
    "intent": "Given a monitor breach, auto-trigger the recovery action",
    "tasks": [
        {"key": "heal", "name": "recover", "capability": "recovery.heal",
         "payload": {"verdict": {"status": "unhealthy",
                                 "breaches": [{"metric": "accuracy", "observed": 0.70, "threshold": 0.85}]},
                     "dataset": "iris", "model_name": "iris-classifier"}},
    ],
}

TEMPLATES["monitor-and-heal"] = {
    "name": "Monitor and heal",
    "intent": "Check health, then heal - the reconcile loop as one workflow",
    "tasks": [
        {"key": "check", "name": "monitor", "capability": "monitor.check",
         "payload": {"metrics": {"accuracy": 0.70, "latency_ms": 200}}},
        {"key": "heal", "name": "recover", "capability": "recovery.heal",
         "payload": {"verdict": {"status": "unhealthy",
                                 "breaches": [{"metric": "accuracy", "observed": 0.70, "threshold": 0.85}]},
                     "dataset": "iris", "model_name": "iris-classifier"},
         "depends_on": ["check"]},
    ],
}


def get_template(name: str) -> dict:
    tpl = TEMPLATES.get(name)
    if tpl is None:
        raise CompileError(f"unknown template '{name}' (have: {sorted(TEMPLATES)})")
    return tpl
