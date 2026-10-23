"""The TypeScript declaration reader and comparer (livemap.dts), on pairs of declaration files
the way the compiler writes them, and on source for the fallback. No Node needed."""
from orchestraty.livemap import dts


def changes(old, new, source=False):
    """{export: (kind, verdict, reasons)} between two versions of one module."""
    a, b = dts.read(old, source), dts.read(new, source)
    return {name: (kind, verdict, reasons) for name, kind, verdict, reasons in dts.compare(a, b)}


def test_a_parameter_removed_or_made_required_is_breaking():
    old = "export declare function charge(amount: number, currency?: string, note?: string): number;\n"
    assert changes(old, "export declare function charge(amount: number, currency?: string): number;\n") == {
        "charge": ("modified", "breaking", ["parameter note removed"])}
    assert changes(old, "export declare function charge(amount: number, currency: string, note?: string): number;\n") == {
        "charge": ("modified", "breaking", ["parameter currency made required"])}
    one, two = "export declare function f(a: number): void;\n", "export declare function f(a: number, b: string): void;\n"
    assert changes(one, two) == {"f": ("modified", "breaking", ["required parameter b added"])}


def test_an_optional_parameter_or_a_new_export_is_compatible():
    old = "export declare function charge(amount: number): number;\n"
    new = old.replace("amount: number)", "amount: number, currency?: string)") + "export declare const LIMIT = 10;\n"
    assert changes(old, new) == {"charge": ("modified", "compatible", ["optional parameter currency added"]),
                                 "LIMIT": ("added", "compatible", ["export added"])}
    renamed = "export declare function charge(cents: number): number;\n"            # calls pass it by position
    assert changes(old, renamed) == {}
    reformatted = "export   declare function charge(\n    amount: number,\n): number;\n"
    assert changes(old, reformatted) == {}


def test_an_export_removed_is_breaking():
    old = "export declare function a(): void;\nexport interface B {\n    x: number;\n}\n"
    assert changes(old, "export declare function a(): void;\n") == {"B": ("removed", "breaking", ["export removed"])}


def test_a_return_type_changed_or_a_type_narrowed_is_for_review():
    assert changes("export declare function f(): number;\n", "export declare function f(): string;\n") == {
        "f": ("modified", "review", ["return type changed"])}
    old = "export declare function f(v: string | number | null): void;\n"
    assert changes(old, "export declare function f(v: string | number): void;\n") == {
        "f": ("modified", "review", ["parameter v: type narrowed"])}
    assert changes(old, "export declare function f(v: string | number | null | undefined): void;\n") == {
        "f": ("modified", "compatible", ["parameter v: type widened"])}
    assert changes(old, "export declare function f(v: Date): void;\n") == {
        "f": ("modified", "review", ["parameter v: type changed"])}
    assert changes('export type Status = "ok" | "failed" | "pending";\n', 'export type Status = "ok" | "failed";\n') == {
        "Status": ("modified", "review", ["type: type narrowed"])}


def test_a_required_property_added_to_an_interface_used_as_an_input_is_breaking():
    old = ("export interface Opts {\n    currency: string;\n}\n"
           "export declare function charge(amount: number, opts: Opts): number;\n"
           "export interface Receipt {\n    id: string;\n}\n")
    new = old.replace("currency: string;", "currency: string;\n    key: string;\n    note?: string;").replace(
        "id: string;", "id: string;\n    total: number;")
    assert changes(old, new) == {
        "Opts": ("modified", "breaking", ["required property Opts.key added", "optional property Opts.note added"]),
        "Receipt": ("modified", "review", ["required property Receipt.total added"])}     # not seen as an input
    assert changes(old, old.replace("    currency: string;\n", "")) == {
        "Opts": ("modified", "breaking", ["property Opts.currency removed"])}


def test_classes_methods_constructors_and_accessors():
    old = ("export declare class Cart {\n    readonly id: string;\n    private secret;\n"
           "    constructor(id: string, owner?: string);\n    add(item: string, qty?: number): this;\n"
           "    get size(): number;\n}\n")
    new = old.replace("add(item: string, qty?: number)", "add(item: string)").replace(
        "constructor(id: string, owner?: string)", "constructor(id: string, owner: string)")
    assert changes(old, new) == {"Cart": ("modified", "breaking", [
        "constructor: parameter owner made required", "Cart.add: parameter qty removed"])}
    assert changes(old, old.replace("    get size(): number;\n", "")) == {
        "Cart": ("modified", "breaking", ["property Cart.size removed"])}
    assert changes(old, old.replace("private secret;", "private other;")) == {}        # private: not the API
    assert changes(old, old.replace("export declare class", "export declare abstract class")) == {
        "Cart": ("modified", "breaking", ["made abstract"])}


def test_overloads_consts_enums_namespaces_and_reexports():
    old = ("export declare function charge(amount: number): number;\n"
           "export declare function charge(amount: Money): number;\n"
           "export declare const total: (a: number, b?: number) => number;\n"
           "export declare enum Color {\n    Red = 0,\n    Green = 1\n}\n"
           "export declare namespace Util {\n    function fmt(n: number): string;\n}\n"
           'export { Money } from "./money";\nexport * from "./money";\n')
    new = (old.replace("export declare function charge(amount: Money): number;\n", "")
              .replace("(a: number, b?: number) => number", "(a: number) => number")
              .replace("    Green = 1\n", "    Blue = 2\n")
              .replace("fmt(n: number)", "fmt(n: number, sep: string)")
              .replace('export * from "./money";\n', ""))
    assert changes(old, new) == {
        "charge": ("modified", "breaking", ["overload (amount: Money): number removed"]),
        "total": ("modified", "breaking", ["parameter b removed"]),
        "Color": ("modified", "breaking", ["member Green removed", "member Blue added"]),
        "Util": ("modified", "breaking", ["fmt: required parameter sep added"]),
        "*": ("modified", "breaking", ["export * from ./money removed"])}


def test_export_lists_and_defaults_name_local_declarations():
    old = ("declare function charge(amount: number): number;\ndeclare const _default: {\n    run(): void;\n};\n"
           "export { charge as pay };\nexport default _default;\n")
    new = old.replace("charge(amount: number)", "charge()")
    assert changes(old, new) == {"pay": ("modified", "breaking", ["parameter amount removed"])}
    module = dts.read(old)
    assert set(module["exports"]) == {"pay", "default"}


def test_the_source_fallback_reads_exports_and_skips_bodies():
    old = ("import { Money } from './money'\n"
           "const RE = /}{/g\n"
           "export function charge(amount: number, currency = 'EUR'): number {\n"
           "  const s = `${amount} {`\n  return amount\n}\n"
           "export const total = (a: number, b?: number): number => a + (b ?? 0)\n"
           "export class Cart {\n  readonly id: string\n  private secret = 1\n"
           "  add(item: string, qty = 1) { return this }\n}\n"
           "export interface Opts { currency: string }\n")
    module = dts.read(old, source=True)
    assert set(module["exports"]) == {"charge", "total", "Cart", "Opts"}
    assert module["imports"] == {"Money": ["./money", "Money"]}
    new = old.replace("currency = 'EUR'", "currency: string").replace("b?: number", "b: number").replace(
        "qty = 1", "qty: number")
    assert changes(old, new, source=True) == {
        "charge": ("modified", "breaking", ["parameter currency made required"]),
        "total": ("modified", "breaking", ["parameter b made required"]),
        "Cart": ("modified", "breaking", ["Cart.add: parameter qty made required"])}


def test_what_cannot_be_read_is_left_out_rather_than_guessed():
    broken = dts.read("export declare function (((\nexport declare function ok(a: number): void;\n")
    assert set(broken) == {"exports", "imports", "stars"} and "ok" not in broken["exports"] or broken["exports"]["ok"]
    weird = "export const x = make()\nexport const y: Foo = 1\n"
    old, new = dts.read(weird, source=True), dts.read(weird.replace("make()", "other()"), source=True)
    assert dts.compare(old, new) == []                        # the type of x is not written: nothing is said


def test_source_caught_mid_edit_does_not_balance():
    def whole(text):
        return dts.balanced(dts.tokens(text, source=True))
    assert whole("export function a(s: string): number {\n  return /[}]/.test(s) ? 1 : 2\n}\n")   # a regex's brace
    assert whole('export const s = "{" + `${a}}`\n')
    assert not whole("export function a() {\n  if (x) {\n    return 1\n}\n")                 # a brace left open
    assert not whole("export const t = `abc\n")
    assert not whole("export const a = 1 /* never closed\n")
    assert not whole("const x = (a]\n")
    # JSX: an apostrophe in its text is no string, and "</" and "/>" start no regular expression
    tsx = ("export function List({ items }: { items: string[] }) {\n"
           "  return <ul>{items.map(i => <li key={i}>Don't {i}</li>)}<Item {...rest} /></ul>\n}\n"
           "export function Other(): null {\n  return null\n}\n")
    assert whole(tsx) and sorted(dts.read(tsx, source=True)["exports"]) == ["List", "Other"]
