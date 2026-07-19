"""Prometheus text-format rendering (pure, no I/O)."""


def render(metrics) -> str:
    """Render Prometheus exposition text.

    metrics: list of (name, type, help, samples) where samples is a list of
    (labels_dict, value).
    """
    lines = []
    for name, mtype, helptext, samples in metrics:
        lines.append(f"# HELP {name} {helptext}")
        lines.append(f"# TYPE {name} {mtype}")
        for labels, value in samples:
            if labels:
                lbl = ",".join(f'{k}="{v}"' for k, v in labels.items())
                lines.append(f"{name}{{{lbl}}} {value}")
            else:
                lines.append(f"{name} {value}")
    return "\n".join(lines) + "\n"
