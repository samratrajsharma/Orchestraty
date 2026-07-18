"""Pure infra planning: spec -> normalized plan -> Terraform HCL (no cloud calls)."""

PROVIDERS = ("local", "aws", "gcp")
RESOURCES = ("k8s_namespace", "gpu_node_pool", "bucket")

_TEMPLATES = {
    "k8s_namespace": '''resource "kubernetes_namespace" "{name}" {{
  metadata {{
    name = "{name}"
  }}
}}
''',
    "gpu_node_pool": '''resource "google_container_node_pool" "{name}" {{
  name       = "{name}"
  node_count = {nodes}
  node_config {{
    machine_type = "{machine}"
    guest_accelerator {{
      type  = "nvidia-tesla-t4"
      count = 1
    }}
  }}
}}
''',
    "bucket": '''resource "aws_s3_bucket" "{name}" {{
  bucket = "{name}"
}}
''',
}


class PlanError(ValueError):
    pass


def build_plan(spec):
    spec = spec or {}
    provider = spec.get("provider", "local")
    if provider not in PROVIDERS:
        raise PlanError(f"unknown provider '{provider}' (have {list(PROVIDERS)})")
    resource = spec.get("resource", "k8s_namespace")
    if resource not in RESOURCES:
        raise PlanError(f"unknown resource '{resource}' (have {list(RESOURCES)})")
    size = spec.get("size") or {}
    if not isinstance(size, dict):
        raise PlanError("size must be an object")
    name = spec.get("name") or f"{resource.replace('_', '-')}-1"
    region = spec.get("region", "us-east-1")
    return {"provider": provider, "resource": resource, "name": name, "size": size, "region": region}


def to_terraform(plan):
    tmpl = _TEMPLATES.get(plan["resource"])
    if tmpl is None:
        raise PlanError(f"no terraform template for '{plan['resource']}'")
    return tmpl.format(
        name=plan["name"],
        nodes=plan["size"].get("nodes", 1),
        machine=plan["size"].get("machine_type", "n1-standard-4"),
    )
