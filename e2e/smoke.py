"""End-to-end smoke test for the live Orchestraty stack (stdlib only).

Run after `docker compose up --build`:
    python e2e/smoke.py

Drives every capability through the control plane and asserts the results.
"""

import json
import os
import sys
import time
import urllib.request

ORCH = os.getenv("ORCH_URL", "http://localhost:8000")
MODEL_SERVER = os.getenv("MODEL_SERVER_URL", "http://localhost:8080")


def _req(method, url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def get(url):
    return _req("GET", url)


def post(url, body=None):
    return _req("POST", url, body)


def wait_healthy(timeout=180):
    for _ in range(timeout):
        try:
            if get(f"{ORCH}/health").get("status") == "healthy":
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1)
    return False


def run_workflow(spec, timeout=120):
    wf = post(f"{ORCH}/workflows", spec)
    wid = wf["id"]
    for _ in range(timeout):
        detail = get(f"{ORCH}/workflows/{wid}")
        if detail["status"] in ("SUCCEEDED", "FAILED", "CANCELLED"):
            return detail
        time.sleep(1)
    return get(f"{ORCH}/workflows/{wid}")


def first_result(detail):
    for t in detail.get("tasks", []):
        if t.get("result"):
            return t["result"]
    return None


results = []


def step(name, fn):
    try:
        ok, detail = fn()
    except Exception as exc:  # noqa: BLE001
        ok, detail = False, repr(exc)
    results.append(ok)
    print(("PASS" if ok else "FAIL"), "-", name, "" if ok else f"({detail})")


def main():
    if not wait_healthy():
        print("orchestrator never became healthy"); sys.exit(1)

    def caps():
        c = get(f"{ORCH}/capabilities")
        names = {x["capability"] for x in c["capabilities"]}
        want = {"echo", "mlops.train", "llm.complete", "data.transform", "infra.provision", "deploy.model"}
        missing = want - names
        return (not missing), f"missing {missing}" if missing else names

    step("capabilities registered", caps)
    step("echo-demo workflow", lambda: (
        (d := run_workflow({"template": "echo-demo"}))["status"] == "SUCCEEDED", d["status"]))
    step("data.transform extracts email", lambda: (
        (r := first_result(run_workflow({"template": "extract-contact"}))) is not None
        and r.get("structured", {}).get("email") == "hello@orchestraty.ai", r))
    step("llm.complete returns a completion", lambda: (
        (r := first_result(run_workflow({"template": "summarize"}))) is not None
        and "completion" in r, r))
    step("infra.provision plans terraform", lambda: (
        (r := first_result(run_workflow({"template": "provision-cluster"}))) is not None
        and r.get("status") == "planned" and "terraform" in r, r))
    step("train-and-serve end to end", lambda: (
        (d := run_workflow({"template": "train-and-serve"}, timeout=180))["status"] == "SUCCEEDED", d["status"]))
    step("model-server serves predictions", lambda: (
        len(post(f"{MODEL_SERVER}/predict",
                 {"inputs": [[5.1, 3.5, 1.4, 0.2], [6.7, 3.0, 5.2, 2.3]]})["predictions"]) == 2, "predict"))

    passed = sum(results)
    print(f"\n{passed}/{len(results)} checks passed")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
