"""
Data Cleaning Pipeline — configurable steps to clean raw datasets.

Steps:
1. Remove duplicate rows
2. Handle null values (drop, fill, interpolate)
3. Normalize text (trim whitespace, case normalization)
4. Standardize date formats
5. Remove outliers (optional, IQR-based)
6. Fix encoding issues
"""

import logging
from dataclasses import dataclass, field, asdict
from typing import Literal

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class CleaningStep:
    """Record of a single cleaning action taken."""
    step: str
    description: str
    rows_before: int
    rows_after: int
    rows_affected: int
    columns_affected: list = field(default_factory=list)


@dataclass
class CleaningReport:
    """Full report of all cleaning steps applied."""
    original_rows: int
    original_columns: int
    final_rows: int
    final_columns: int
    steps: list = field(default_factory=list)  # list of CleaningStep dicts
    total_rows_removed: int = 0
    total_nulls_filled: int = 0

    def to_dict(self):
        return asdict(self)


def remove_duplicates(df: pd.DataFrame) -> tuple[pd.DataFrame, CleaningStep]:
    """Remove exact duplicate rows."""
    before = len(df)
    df = df.drop_duplicates().reset_index(drop=True)
    after = len(df)
    step = CleaningStep(
        step="remove_duplicates",
        description=f"Removed {before - after} duplicate rows",
        rows_before=before,
        rows_after=after,
        rows_affected=before - after,
    )
    logger.info("Dedup: %d → %d rows (%d removed)", before, after, before - after)
    return df, step


def handle_nulls(
    df: pd.DataFrame,
    strategy: Literal["drop_rows", "fill_mean", "fill_median", "fill_mode", "fill_empty"] = "fill_mode",
) -> tuple[pd.DataFrame, CleaningStep]:
    """Handle null values based on the chosen strategy."""
    before = len(df)
    total_nulls_before = int(df.isna().sum().sum())
    affected_cols = [c for c in df.columns if df[c].isna().any()]

    if strategy == "drop_rows":
        df = df.dropna().reset_index(drop=True)
    elif strategy == "fill_mean":
        for col in df.select_dtypes(include=[np.number]).columns:
            df[col] = df[col].fillna(df[col].mean())
        for col in df.select_dtypes(exclude=[np.number]).columns:
            df[col] = df[col].fillna(df[col].mode().iloc[0] if not df[col].mode().empty else "")
    elif strategy == "fill_median":
        for col in df.select_dtypes(include=[np.number]).columns:
            df[col] = df[col].fillna(df[col].median())
        for col in df.select_dtypes(exclude=[np.number]).columns:
            df[col] = df[col].fillna(df[col].mode().iloc[0] if not df[col].mode().empty else "")
    elif strategy == "fill_mode":
        for col in df.columns:
            mode = df[col].mode()
            if not mode.empty:
                df[col] = df[col].fillna(mode.iloc[0])
    elif strategy == "fill_empty":
        df = df.fillna("")

    after = len(df)
    total_nulls_after = int(df.isna().sum().sum())

    step = CleaningStep(
        step="handle_nulls",
        description=f"Strategy: {strategy}. Nulls: {total_nulls_before} → {total_nulls_after}",
        rows_before=before,
        rows_after=after,
        rows_affected=before - after,
        columns_affected=affected_cols,
    )
    logger.info("Nulls: %s, %d → %d nulls", strategy, total_nulls_before, total_nulls_after)
    return df, step


def normalize_text(df: pd.DataFrame) -> tuple[pd.DataFrame, CleaningStep]:
    """Trim whitespace and normalize text columns."""
    text_cols = df.select_dtypes(include=["object"]).columns.tolist()
    changes = 0

    for col in text_cols:
        original = df[col].copy()
        df[col] = df[col].astype(str).str.strip()
        # Count actual changes
        changed = (original.astype(str) != df[col]).sum()
        changes += int(changed)

    step = CleaningStep(
        step="normalize_text",
        description=f"Trimmed whitespace in {len(text_cols)} text columns, {changes} values changed",
        rows_before=len(df),
        rows_after=len(df),
        rows_affected=changes,
        columns_affected=text_cols,
    )
    logger.info("Text normalization: %d columns, %d values changed", len(text_cols), changes)
    return df, step


def remove_outliers(
    df: pd.DataFrame,
    iqr_multiplier: float = 1.5,
) -> tuple[pd.DataFrame, CleaningStep]:
    """Remove rows with numeric outliers using IQR method."""
    before = len(df)
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    mask = pd.Series([True] * len(df), index=df.index)

    for col in numeric_cols:
        q1 = df[col].quantile(0.25)
        q3 = df[col].quantile(0.75)
        iqr = q3 - q1
        lower = q1 - iqr_multiplier * iqr
        upper = q3 + iqr_multiplier * iqr
        mask &= (df[col] >= lower) & (df[col] <= upper) | df[col].isna()

    df = df[mask].reset_index(drop=True)
    after = len(df)

    step = CleaningStep(
        step="remove_outliers",
        description=f"IQR method (multiplier={iqr_multiplier}), removed {before - after} outlier rows",
        rows_before=before,
        rows_after=after,
        rows_affected=before - after,
        columns_affected=numeric_cols,
    )
    logger.info("Outliers: %d → %d rows (%d removed)", before, after, before - after)
    return df, step


def run_cleaning_pipeline(
    df: pd.DataFrame,
    null_strategy: str = "fill_mode",
    remove_dupes: bool = True,
    normalize: bool = True,
    handle_outliers: bool = False,
    iqr_multiplier: float = 1.5,
) -> tuple[pd.DataFrame, CleaningReport]:
    """Run the full cleaning pipeline on a DataFrame.

    Args:
        df: Input DataFrame.
        null_strategy: How to handle nulls (drop_rows, fill_mean, fill_median, fill_mode, fill_empty).
        remove_dupes: Whether to remove duplicate rows.
        normalize: Whether to normalize text columns.
        handle_outliers: Whether to remove numeric outliers.
        iqr_multiplier: IQR multiplier for outlier detection.

    Returns:
        Tuple of (cleaned DataFrame, CleaningReport).
    """
    original_rows = len(df)
    original_cols = len(df.columns)
    steps = []

    # Step 1: Remove duplicates
    if remove_dupes:
        df, step = remove_duplicates(df)
        steps.append(asdict(step))

    # Step 2: Handle nulls
    df, step = handle_nulls(df, strategy=null_strategy)
    steps.append(asdict(step))

    # Step 3: Normalize text
    if normalize:
        df, step = normalize_text(df)
        steps.append(asdict(step))

    # Step 4: Remove outliers (optional)
    if handle_outliers:
        df, step = remove_outliers(df, iqr_multiplier=iqr_multiplier)
        steps.append(asdict(step))

    report = CleaningReport(
        original_rows=original_rows,
        original_columns=original_cols,
        final_rows=len(df),
        final_columns=len(df.columns),
        steps=steps,
        total_rows_removed=original_rows - len(df),
        total_nulls_filled=int(df.isna().sum().sum()),
    )

    logger.info(
        "Cleaning complete: %d → %d rows, %d steps applied",
        original_rows, len(df), len(steps),
    )
    return df, report
