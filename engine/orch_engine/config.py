from __future__ import annotations
import os
from dataclasses import dataclass


@dataclass
class Config:
    data_dir: str = os.getenv("LEGEND_DATA_DIR", ".legend_cache")   # parse/graph cache, clones, snapshots
    use_cache: bool = os.getenv("LEGEND_USE_CACHE", "1") not in ("0", "false", "False")


CONFIG = Config()
