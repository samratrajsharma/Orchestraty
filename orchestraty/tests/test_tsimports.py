"""TypeScript imports (livemap.tsimports): specifiers resolved through relative paths and a
tsconfig's baseUrl and paths aliases, and the files that import and use an export. No Node."""
from orchestraty.livemap import tsimports


def copy(files):
    """(Resolver, Imports) over a copy of the code given as {posix path: text}."""
    resolver = tsimports.Resolver(set(files), files.get)
    texts = {p: t for p, t in files.items() if p.endswith(tsimports.CODE_EXTS)}
    return resolver, tsimports.Imports(texts, resolver)


BASE = """{
  // a base config, with comments and trailing commas
  "compilerOptions": {
    "baseUrl": "./src",
    "paths": {"@lib/*": ["lib/*", "fallback/*"], "@pay": ["lib/pay/index.ts"],},
  },
}
"""


def test_aliases_from_a_tsconfig_and_its_extends():
    files = {
        "tsconfig.base.json": BASE,
        "tsconfig.json": '{"extends": "./tsconfig.base", "compilerOptions": {"strict": true}}',
        "src/lib/pay/index.ts": "export function charge(a: number) { return a }\n",
        "src/lib/money.ts": "export const x = 1\n",
        "src/fallback/only.ts": "export const y = 1\n",
        "src/app/main.ts": "",
        "src/util.ts": "",
    }
    r, _ = copy(files)
    assert r.resolve("src/app/main.ts", "@lib/money") == "src/lib/money.ts"           # paths, under baseUrl
    assert r.resolve("src/app/main.ts", "@lib/only") == "src/fallback/only.ts"        # the second target
    assert r.resolve("src/app/main.ts", "@pay") == "src/lib/pay/index.ts"             # an exact pattern
    assert r.resolve("src/app/main.ts", "util") == "src/util.ts"                      # baseUrl alone
    assert r.resolve("src/app/main.ts", "../lib/money.js") == "src/lib/money.ts"      # .js names the .ts
    assert r.resolve("src/app/main.ts", "../lib/pay") == "src/lib/pay/index.ts"       # a folder's index
    assert r.resolve("src/app/main.ts", "react") is None                              # a package
    assert r.resolve("src/app/main.ts", "@lib/nothing") is None


def test_paths_without_a_base_url_are_relative_to_their_tsconfig():
    files = {"packages/web/tsconfig.json": '{"compilerOptions": {"paths": {"~/*": ["./src/*"]}}}',
             "packages/web/src/a.ts": "", "packages/web/src/deep/b.ts": ""}
    r, _ = copy(files)
    assert r.resolve("packages/web/src/deep/b.ts", "~/a") == "packages/web/src/a.ts"   # the nearest tsconfig
    assert tsimports.lenient_json('{"a": "// not a comment", /* x */ "b": [1,],}') == {"a": "// not a comment",
                                                                                      "b": [1]}
    assert tsimports.lenient_json("{ nope") is None


PAY = "export function charge(amount: number, currency: string): number { return amount }\nexport const other = 1\n"


def test_a_use_needs_import_evidence():
    files = {
        "tsconfig.json": '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}}',
        "src/pay.ts": PAY,
        "src/index.ts": 'export { charge as pay } from "./pay"\nexport * from "./pay"\n',
        "src/named.ts": 'import { charge } from "./pay"\n\nexport const a = charge(1, "EUR")\n',
        "src/aliased.ts": 'import { charge as c } from "@/pay"\nconst x = 1\nexport const b = () => c(2, "EUR")\n',
        "src/namespace.ts": 'import * as payments from "./pay"\nexport const n = payments.charge(3, "EUR")\n'
                            "payments.other\n",
        "src/barrel.ts": 'import { pay, charge } from "./index"\nexport const d = pay(4, "EUR") + charge(5, "EUR")\n',
        "src/unrelated.ts": "function charge(x: number) { return x }\nexport const e = charge(1)\n",
        "src/typeonly.ts": 'import type { charge } from "./pay"\n',
        "src/string.ts": 'import { other } from "./pay"\nexport const s = "charge(1)" + other\n',
    }
    _, imports = copy(files)
    assert imports.uses("src/pay.ts", "charge") == {
        "src/named.ts": [3], "src/aliased.ts": [3], "src/namespace.ts": [2], "src/barrel.ts": [2]}
    assert imports.uses("src/pay.ts", "other") == {"src/namespace.ts": [3], "src/string.ts": [2]}
    assert imports.origin("src/index.ts", "pay") == ("src/pay.ts", "charge")
    assert imports.origin("src/index.ts", "other") == ("src/pay.ts", "other")           # through export *


def test_a_default_export_and_its_local_name():
    files = {"src/pay.ts": "export default function charge(a: number) { return a }\n",
             "src/use.ts": 'import pay from "./pay"\nexport const u = pay(1)\n'}
    _, imports = copy(files)
    assert imports.uses("src/pay.ts", "default") == {"src/use.ts": [2]}
