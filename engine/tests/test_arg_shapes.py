"""How each Python call passes its arguments: recorded per call site, carried on calls edges."""
from __future__ import annotations

import ast

import pytest

from orch_engine.graph import build_graph
from orch_engine.parsing import arg_shape, parse_js, parse_python, site_args


@pytest.mark.parametrize("src,shape", [
    ("f()", (0, False, (), False)),
    ("f(1, 2)", (2, False, (), False)),
    ("f(a=1)", (0, False, ("a",), False)),
    ("f(1, b=2, a=3)", (1, False, ("a", "b"), False)),
    ("f(*xs)", (0, True, (), False)),
    ("f(1, *xs, 2)", (2, True, (), False)),
    ("f(**kw)", (0, False, (), True)),
    ("f(1, *xs, c=3, **kw)", (1, True, ("c",), True)),
])
def test_arg_shape(src, shape):
    assert arg_shape(ast.parse(src).body[0].value) == shape


def graph_of(files):
    return build_graph([parse_python(p, s) for p, s in files.items()])


def test_a_calls_edge_carries_how_each_call_passes_its_arguments():
    g = graph_of({
        "a.py": "def f(x, y=0):\n    return x\n",
        "b.py": "from a import f\n\n\ndef g(xs, kw):\n    f(1, y=2)\n    f(3, 4)\n    f(*xs, **kw)\n    f(1, y=2)\n",
    })
    info = g.edge_info("b.py::g", "a.py::f")
    assert info["evidence"] == "import" and info["lines"] == [5, 6, 7, 8]
    assert info["args"] == [
        {"positional": 0, "star": True, "keywords": [], "double_star": True, "on_class": False},
        {"positional": 1, "star": False, "keywords": ["y"], "double_star": False, "on_class": False},
        {"positional": 2, "star": False, "keywords": [], "double_star": False, "on_class": False},
    ]


def test_two_calls_on_one_line_are_both_kept():
    g = graph_of({"a.py": "def f(x, y=0):\n    return x\n\n\ndef g():\n    return f(1) + f(x=2)\n"})
    shapes = g.edge_info("a.py::g", "a.py::f")["args"]
    assert [s["positional"] for s in shapes] == [0, 1] and shapes[0]["keywords"] == ["x"]


def test_method_calls_count_the_arguments_after_the_receiver():
    g = graph_of({"m.py": "class C:\n    def m(self, a, b=1):\n        return a\n\n\n"
                          "def use():\n    c = C()\n    return c.m(5, b=6)\n"})
    assert g.edge_info("m.py::use", "m.py::C.m")["args"] == [
        {"positional": 1, "star": False, "keywords": ["b"], "double_star": False, "on_class": False}]


def test_a_call_says_whether_it_is_made_on_the_class():
    g = graph_of({"m.py": "class C:\n    def m(self, a):\n        return a\n\n"
                          "    @staticmethod\n    def s(a):\n        return a\n\n"
                          "    def on_self(self):\n        return self.s(1)\n\n"
                          "    @classmethod\n    def on_cls(cls):\n        return cls.s(1)\n\n\n"
                          "def use(c: C):\n    c.m(1)\n    C.m(c, 1)\n"})
    one = {"positional": 1, "star": False, "keywords": [], "double_star": False}
    assert g.edge_info("m.py::use", "m.py::C.m")["args"] == [dict(one, on_class=False), dict(one, on_class=True)]
    assert g.edge_info("m.py::C.on_self", "m.py::C.s")["args"] == [dict(one, on_class=False)]
    assert g.edge_info("m.py::C.on_cls", "m.py::C.s")["args"] == [dict(one, on_class=True)]


def test_edges_without_python_calls_have_no_args():
    g = graph_of({"base.py": "class Base:\n    pass\n",
                  "sub.py": "from base import Base\n\n\nclass Sub(Base):\n    pass\n"})
    assert "args" not in g.edge_info("sub.py::Sub", "base.py::Base", "inherits")
    js = parse_js("m.js", "function f(x) { return x; }\nfunction g() { return f(1); }\n", "javascript")
    assert all(site_args(site) is None for sym in js.symbols for site in sym.call_sites)
    assert "args" not in build_graph([js]).edge_info("m.js::g", "m.js::f")


def test_a_method_called_through_its_class_counts_the_arguments_after_the_receiver():
    g = graph_of({"m.py": "class C:\n    def m(self, a, b=1):\n        return a\n\n"
                          "    @classmethod\n    def k(cls, a):\n        return a\n\n"
                          "    @staticmethod\n    def s(a):\n        return a\n\n\n"
                          "def use(c):\n    C.m(c, 5)\n    C.k(5)\n    C.s(5)\n    C.m(*c)\n"})
    one = {"positional": 1, "star": False, "keywords": [], "double_star": False, "on_class": True}
    assert g.edge_info("m.py::use", "m.py::C.m")["args"] == [
        {"positional": 0, "star": True, "keywords": [], "double_star": False, "on_class": True}, one]
    assert g.edge_info("m.py::use", "m.py::C.k")["args"] == [one]
    assert g.edge_info("m.py::use", "m.py::C.s")["args"] == [one]
