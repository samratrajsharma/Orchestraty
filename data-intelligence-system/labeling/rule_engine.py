"""
Rule-Based Labeling Engine — applies configurable rules to assign labels.
"""

import re
import logging
from dataclasses import dataclass, field, asdict
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

# ── Supported operators ─────────────────────────────────────────────────

SUPPORTED_OPERATORS = frozenset({
    "equals",
    "not_equals",
    "contains",
    "not_contains",
    "starts_with",
    "ends_with",
    "greater_than",
    "less_than",
    "greater_equal",
    "less_equal",
    "regex_match",
    "in_list",
    "is_null",
    "is_not_null",
})


# ── Data classes ────────────────────────────────────────────────────────

@dataclass
class LabelingRule:
    """A single labeling rule definition."""
    column: str
    operator: str
    value: Any
    label: str
    priority: int = 0


@dataclass
class LabelingReport:
    """Summary report produced after labeling a dataset."""
    total_rows: int
    labeled_count: int
    unlabeled_count: int
    label_distribution: dict = field(default_factory=dict)   # label -> count
    rules_applied: int = 0
    conflict_count: int = 0
    per_rule_stats: list = field(default_factory=list)       # [{rule_index, matches, label}]
    warnings: list = field(default_factory=list)             # human-readable warnings

    def to_dict(self):
        return asdict(self)


# ── Rule Engine ─────────────────────────────────────────────────────────

class RuleEngine:
    """Evaluate a list of labeling rules against a pandas DataFrame.

    Args:
        conflict_strategy: How to resolve when multiple rules match a row.
            * ``"first_match"``      — first matching rule wins (list order).
            * ``"highest_priority"`` — rule with highest ``priority`` wins.
            * ``"all"``              — comma-join all matching labels.
    """

    def __init__(self, conflict_strategy: str = "first_match"):
        if conflict_strategy not in ("first_match", "highest_priority", "all"):
            raise ValueError(
                f"Unknown conflict_strategy '{conflict_strategy}'. "
                "Choose from: first_match, highest_priority, all"
            )
        self.conflict_strategy = conflict_strategy
        self.rules: list[LabelingRule] = []

    # ── Rule loading ────────────────────────────────────────────────────

    def load_rules(self, rule_defs: list[dict]) -> None:
        """Parse a list of plain dicts into :class:`LabelingRule` objects.

        Raises ``ValueError`` if any rule uses an unsupported operator.
        """
        self.rules = []
        for idx, rd in enumerate(rule_defs):
            operator = rd.get("operator", "")
            if operator not in SUPPORTED_OPERATORS:
                raise ValueError(
                    f"Rule {idx}: unsupported operator '{operator}'. "
                    f"Supported: {sorted(SUPPORTED_OPERATORS)}"
                )
            self.rules.append(
                LabelingRule(
                    column=rd["column"],
                    operator=operator,
                    value=rd.get("value"),
                    label=rd["label"],
                    priority=rd.get("priority", 0),
                )
            )
        logger.info("Loaded %d labeling rules.", len(self.rules))

    # ── Single-rule evaluation ──────────────────────────────────────────

    @staticmethod
    def _evaluate_rule(rule: LabelingRule, value: Any) -> bool:  # noqa: C901
        """Return *True* if *value* satisfies the rule's operator + value."""
        op = rule.operator
        target = rule.value

        # Null checks (independent of target)
        if op == "is_null":
            return pd.isna(value)
        if op == "is_not_null":
            return not pd.isna(value)

        # For remaining operators a null cell never matches
        if pd.isna(value):
            return False

        # --- String operators (coerce both sides) ---
        if op in ("contains", "not_contains", "starts_with", "ends_with"):
            str_val = str(value)
            str_target = str(target)
            if op == "contains":
                return str_target in str_val
            if op == "not_contains":
                return str_target not in str_val
            if op == "starts_with":
                return str_val.startswith(str_target)
            if op == "ends_with":
                return str_val.endswith(str_target)

        # --- Regex ---
        if op == "regex_match":
            try:
                return bool(re.search(str(target), str(value)))
            except re.error as exc:
                logger.warning("Invalid regex '%s': %s", target, exc)
                return False

        # --- in_list ---
        if op == "in_list":
            if not isinstance(target, list):
                logger.warning("in_list expects a list value; got %s", type(target).__name__)
                return False
            return value in target

        # --- Equality ---
        if op == "equals":
            try:
                return value == target or _coerce_numeric(value) == _coerce_numeric(target)
            except (TypeError, ValueError):
                return str(value) == str(target)

        if op == "not_equals":
            try:
                return value != target and _coerce_numeric(value) != _coerce_numeric(target)
            except (TypeError, ValueError):
                return str(value) != str(target)

        # --- Comparison operators (numeric coercion) ---
        if op in ("greater_than", "less_than", "greater_equal", "less_equal"):
            try:
                num_val = _coerce_numeric(value)
                num_target = _coerce_numeric(target)
            except (TypeError, ValueError):
                logger.debug(
                    "Cannot compare '%s' and '%s' numerically; skipping.", value, target
                )
                return False

            if op == "greater_than":
                return num_val > num_target
            if op == "less_than":
                return num_val < num_target
            if op == "greater_equal":
                return num_val >= num_target
            if op == "less_equal":
                return num_val <= num_target

        return False

    # ── Apply all rules to a DataFrame ──────────────────────────────────

    def apply(
        self,
        df: pd.DataFrame,
        label_column: str = "__label__",
    ) -> tuple[pd.DataFrame, LabelingReport]:
        """Label every row in *df* by evaluating all loaded rules.

        Args:
            df: Input DataFrame (not mutated — a copy is returned).
            label_column: Name of the new column that will hold labels.

        Returns:
            A ``(labeled_df, report)`` tuple.
        """
        if not self.rules:
            logger.warning("No rules loaded — returning unlabeled DataFrame.")
            out = df.copy()
            out[label_column] = None
            report = LabelingReport(
                total_rows=len(df),
                labeled_count=0,
                unlabeled_count=len(df),
            )
            return out, report

        if df.empty:
            logger.info("Empty DataFrame — nothing to label.")
            out = df.copy()
            out[label_column] = None
            report = LabelingReport(total_rows=0, labeled_count=0, unlabeled_count=0)
            return out, report

        out = df.copy()

        # Pre-check which rules target valid columns
        valid_columns = set(df.columns)
        rule_column_valid = []
        _column_warnings: list[str] = []
        for idx, rule in enumerate(self.rules):
            if rule.column not in valid_columns:
                logger.warning(
                    "Rule %d targets column '%s' which does not exist — skipping.",
                    idx, rule.column,
                )
                rule_column_valid.append(False)
                _column_warnings.append(
                    f"Rule {idx} targets column '{rule.column}' which does not exist in the dataset. "
                    f"Available columns: {sorted(valid_columns)}"
                )
            else:
                rule_column_valid.append(True)

        # Per-rule match counters
        per_rule_matches: list[int] = [0] * len(self.rules)
        conflict_count = 0
        labels: list[Any] = [None] * len(df)

        for row_idx in range(len(df)):
            matched_rules: list[tuple[int, LabelingRule]] = []

            for rule_idx, rule in enumerate(self.rules):
                if not rule_column_valid[rule_idx]:
                    continue
                cell_value = df.iat[row_idx, df.columns.get_loc(rule.column)]
                try:
                    if self._evaluate_rule(rule, cell_value):
                        matched_rules.append((rule_idx, rule))
                        per_rule_matches[rule_idx] += 1
                except Exception:
                    logger.debug(
                        "Rule %d evaluation error on row %d; skipping.",
                        rule_idx, row_idx, exc_info=True,
                    )

            if not matched_rules:
                continue

            if len(matched_rules) > 1:
                conflict_count += 1

            # Resolve conflict
            if self.conflict_strategy == "first_match":
                labels[row_idx] = matched_rules[0][1].label

            elif self.conflict_strategy == "highest_priority":
                winner = max(matched_rules, key=lambda t: t[1].priority)
                labels[row_idx] = winner[1].label

            elif self.conflict_strategy == "all":
                labels[row_idx] = ",".join(r.label for _, r in matched_rules)

        out[label_column] = labels

        # Build report
        labeled_count = sum(1 for lbl in labels if lbl is not None)
        label_distribution: dict[str, int] = {}
        for lbl in labels:
            if lbl is not None:
                for single_label in str(lbl).split(","):
                    single_label = single_label.strip()
                    label_distribution[single_label] = label_distribution.get(single_label, 0) + 1

        per_rule_stats = [
            {"rule_index": idx, "matches": per_rule_matches[idx], "label": rule.label}
            for idx, rule in enumerate(self.rules)
        ]

        report = LabelingReport(
            total_rows=len(df),
            labeled_count=labeled_count,
            unlabeled_count=len(df) - labeled_count,
            label_distribution=label_distribution,
            rules_applied=len(self.rules),
            conflict_count=conflict_count,
            per_rule_stats=per_rule_stats,
            warnings=_column_warnings,
        )

        logger.info(
            "Labeling complete: %d/%d rows labeled, %d conflicts (%s strategy), "
            "distribution=%s",
            labeled_count, len(df), conflict_count, self.conflict_strategy,
            label_distribution,
        )
        return out, report


# ── Helpers ─────────────────────────────────────────────────────────────

def _coerce_numeric(val: Any) -> float:
    """Best-effort conversion to float for comparison operators."""
    if isinstance(val, (int, float)):
        return float(val)
    return float(val)
