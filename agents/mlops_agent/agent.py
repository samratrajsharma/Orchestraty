"""MLOps training agent.

Capability: mlops.train. Trains a scikit-learn model on a built-in dataset,
logs params + metrics to MLflow, and registers the model. Blocking ML work runs
in a worker thread so the async consume loop stays responsive.
"""

import asyncio
import logging
import os

from sdk.base_agent import BaseAgent

from .config import resolve_config

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("mlops-agent")


class MLOpsAgent(BaseAgent):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.tracking_uri = os.getenv("MLFLOW_TRACKING_URI", "http://mlflow:5000")
        self.experiment = os.getenv("MLFLOW_EXPERIMENT", "orchestraty")

    async def handle(self, payload: dict) -> dict:
        return await asyncio.to_thread(self._train, payload)

    def _train(self, payload: dict) -> dict:
        import mlflow
        import mlflow.sklearn
        from sklearn.datasets import load_iris, load_wine
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import accuracy_score, f1_score
        from sklearn.model_selection import train_test_split

        cfg = resolve_config(payload)
        loaders = {"iris": load_iris, "wine": load_wine}
        data = loaders[cfg["dataset"]]()
        x_tr, x_te, y_tr, y_te = train_test_split(
            data.data, data.target, test_size=0.25, random_state=42
        )
        if cfg["model"] == "random_forest":
            model = RandomForestClassifier(random_state=42, **cfg["params"])
        else:
            model = LogisticRegression(max_iter=1000, **cfg["params"])

        mlflow.set_tracking_uri(self.tracking_uri)
        mlflow.set_experiment(self.experiment)
        with mlflow.start_run() as run:
            model.fit(x_tr, y_tr)
            preds = model.predict(x_te)
            acc = float(accuracy_score(y_te, preds))
            f1 = float(f1_score(y_te, preds, average="macro"))
            mlflow.log_params({"dataset": cfg["dataset"], "model": cfg["model"], **cfg["params"]})
            mlflow.log_metrics({"accuracy": acc, "f1_macro": f1})
            info = mlflow.sklearn.log_model(
                model, artifact_path="model",
                registered_model_name=cfg["registered_model_name"],
            )
            log.info("trained %s on %s: accuracy=%.3f", cfg["model"], cfg["dataset"], acc)
            return {
                "run_id": run.info.run_id,
                "metrics": {"accuracy": acc, "f1_macro": f1},
                "model_uri": info.model_uri,
                "registered_model": cfg["registered_model_name"],
                "tracking_uri": self.tracking_uri,
            }


if __name__ == "__main__":
    agent = MLOpsAgent(name=os.getenv("AGENT_NAME", "mlops-agent-1"), capabilities=["mlops.train"])
    asyncio.run(agent.run())
