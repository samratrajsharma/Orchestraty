"""Deployment artifact generation (pure): Dockerfile + Kubernetes manifests."""


def dockerfile(name: str) -> str:
    return (
        "FROM python:3.11-slim\n"
        "RUN pip install --no-cache-dir mlflow scikit-learn fastapi uvicorn numpy\n"
        f"ENV MODEL_URI=models:/{name}/latest\n"
        "COPY serve.py .\n"
        'CMD ["python", "serve.py"]\n'
    )


def k8s(name: str, image: str = None, replicas: int = 2, port: int = 8080) -> str:
    image = image or f"orchestraty/{name}:latest"
    return (
        "apiVersion: apps/v1\n"
        "kind: Deployment\n"
        "metadata:\n"
        f"  name: {name}\n"
        "spec:\n"
        f"  replicas: {replicas}\n"
        "  selector:\n"
        "    matchLabels:\n"
        f"      app: {name}\n"
        "  template:\n"
        "    metadata:\n"
        "      labels:\n"
        f"        app: {name}\n"
        "    spec:\n"
        "      containers:\n"
        f"        - name: {name}\n"
        f"          image: {image}\n"
        "          ports:\n"
        f"            - containerPort: {port}\n"
        "---\n"
        "apiVersion: v1\n"
        "kind: Service\n"
        "metadata:\n"
        f"  name: {name}\n"
        "spec:\n"
        "  selector:\n"
        f"    app: {name}\n"
        "  ports:\n"
        "    - port: 80\n"
        f"      targetPort: {port}\n"
    )
