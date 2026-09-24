from __future__ import annotations
import os
from dataclasses import dataclass


@dataclass
class Config:
    data_dir: str = os.getenv("ORCH_DATA_DIR", ".orch_cache")   # parse/graph cache, clones, snapshots
    use_cache: bool = os.getenv("ORCH_USE_CACHE", "1") not in ("0", "false", "False")


CONFIG = Config()
