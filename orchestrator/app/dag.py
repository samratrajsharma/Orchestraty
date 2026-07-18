"""Dependency-graph helpers for a workflow's tasks (pure logic)."""

from collections import deque

from .enums import TaskStatus


def validate_dag(tasks) -> None:
    ids = {t["id"] for t in tasks}
    graph = {}
    for t in tasks:
        for dep in t["depends_on"]:
            if dep not in ids:
                raise ValueError(f"task {t['id']} depends on unknown task {dep}")
        graph[t["id"]] = list(t["depends_on"])
    WHITE, GREY, BLACK = 0, 1, 2
    color = {n: WHITE for n in graph}

    def visit(n):
        color[n] = GREY
        for m in graph[n]:
            if color[m] == GREY:
                raise ValueError(f"cycle detected involving {m}")
            if color[m] == WHITE:
                visit(m)
        color[n] = BLACK

    for n in graph:
        if color[n] == WHITE:
            visit(n)


def ready_task_ids(tasks):
    status = {t["id"]: t["status"] for t in tasks}
    out = []
    for t in tasks:
        if t["status"] != TaskStatus.QUEUED.value:
            continue
        if all(status.get(d) == TaskStatus.SUCCESS.value for d in t["depends_on"]):
            out.append(t["id"])
    return out


def topological_order(tasks):
    ids = {t["id"] for t in tasks}
    deps = {t["id"]: set(t["depends_on"]) for t in tasks}
    indeg = {i: len(deps[i]) for i in ids}
    dependents = {i: [] for i in ids}
    for i in ids:
        for d in deps[i]:
            dependents[d].append(i)
    q = deque(sorted(i for i in ids if indeg[i] == 0))
    order = []
    while q:
        n = q.popleft()
        order.append(n)
        for m in dependents[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                q.append(m)
    if len(order) != len(ids):
        raise ValueError("cycle detected")
    return order
