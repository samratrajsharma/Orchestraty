# Shared spike code

Status: throwaway (Phase 0). Not part of the product.

This folder holds code that more than one spike uses, so that spikes never import each other. Today that is the effects module: given how a function's signature changed and where it is called, it says what the change means for its callers, and says just as plainly what it could not see.

Run the tests from `spikes/` with `python -m pytest _shared`. Python 3.9 or later, standard library only.

## The input record (`effects_record.py`)

Every part of the module reads one small record per function, never the engine's internals. A thin adapter fills it; the tests build it from synthetic Python source.

| Record | Fields |
| --- | --- |
| `FunctionChange` | `id`, `name`, `file`, `line`, `language`, `before` and `after` (a `Signature`, or `None` when the function was added or removed), `call_sites`, `still_resolves` (after a removal: whether the name still resolves, for example after a move or a re-export; `None` when not recorded) |
| `Signature` | `params` in order, `is_async`, `returns`, `decorators`, `implicit_first` (a `self` or `cls` that callers never pass; it is kept out of `params`) |
| `Param` | `name`, `kind` (`positional_only`, `positional_or_keyword`, `var_positional`, `keyword_only`, `var_keyword`), `has_default`, `default` (source text, when known), `annotation` |
| `CallSite` | `file`, `line`, `positional` (how many plain positional arguments), `keywords` (their names), `star_args` and `star_kwargs` (a `*expr` or `**expr`), `passes_first` (`Cls.m(obj, ...)`), `resolved` (a resolved edge, or found by name only), `evidence` (the resolver's tag) |

Records round-trip through JSON (`to_dict`, `from_dict`). A signature Python would reject (repeated names, parameters out of order, a default before a non-default) is refused. `signature_from_source` and `call_site_from_node` build records from Python source with the `ast` module.

## What it does

| Module | What it answers |
| --- | --- |
| `effects_classify.py` | Is the contract change **breaking**, does it **need review**, or is it **compatible**? From the callers' side, by Python's calling rules: required, optional, keyword-only, `*args` and `**kwargs` parameters; defaults added, removed or changed; renames; reordering; a new positional slot that takes values `*args` took; a new keyword that `**kwargs` took; async; decorators; removals. |
| `effects_binding.py` | Does each call site still **bind** to the new signature? Each call is `breaks`, `rebinds` (an argument now reaches another parameter), `binds`, or `needs review`. `callers_not_updated` lists the ones that break or rebind, with file and line. |
| `effects_names.py` | **Name-only references:** uses of the function's name that no resolved edge covers, such as decorator uses, callbacks, module-level callers, inherited methods and `super()` calls. Python files are read with the tokenizer, so comments and strings never count. |
| `effects_testfiles.py` | **Test files** by path: `test_*.py`, `*_test.py`, `conftest.py`, `tests.py`, `*.test.ts` and `*.spec.ts` (and the other JS extensions), `__tests__/`, `tests/` and `test/`. Which references to a function come from tests. |
| `effects_notanalysed.py` | The fixed **Not analysed** block: the same list of blind spots every time, then what this change left out (other languages, skipped folders, symlinks, submodules, oversized files). |

## Confidence

No high-confidence statement comes from anything but parsed signatures or resolved edges.

- **Classes:** `breaking` and `compatible` are high-confidence; `needs review` never is. A decorator that may wrap the function (anything but `staticmethod`, `classmethod`, `abstractmethod`, `override`, `final`, `lru_cache`, `cache`, `contextmanager`, `asynccontextmanager` and route decorators of the form `app.get("/path")`; `mock.patch(...)` is not one) turns both into `needs review`. A change to or from async is `needs review` (whether callers await is not recorded), and so is a removal unless the record says the name no longer resolves.
- **Call binding:** a result is high-confidence only for a resolved edge whose binding was decided. Argument unpacking that may fill or clash with a parameter, a parameter renamed in place, a keyword now taken by `**kwargs`, a changed default the call relies on, a `self` passed by keyword, an async change, or a removed function whose name may still resolve is `needs review`, never `binds`. A call through the class (`Cls.m(obj, ...)`) passes `self` itself; a classmethod still binds `cls`. A call found by name only keeps its result but is labelled `name-only`, low confidence.
- **Name-only references** are always low confidence.

## Python only

The classes and the binding rules are Python's. Every other language is `not analysed`, and the Not analysed block lists its changed files.
