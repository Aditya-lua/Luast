"""Tests for the LUAST junk-predicate (Phi) and nested-pool machinery.

Covers the failure modes observed on the 4.1 MB ps2 input:
  * nested constant-table reads (pool[slot][key]) were never collected
  * alias reads (local a = pool[slot]; a[key]) never resolved
  * in-place writes through aliases corrupted top-level pool state
  * opaque junk predicates forked dispatcher recovery exponentially
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from luau_recover.analysis import analyze
from luau_recover.controlflow import DispatcherPass
from luau_recover.emitter import emit
from luau_recover.parser import parse
from luau_recover.passes import PassStats, Phi, PoolResolver, UNKNOWN, evaluate, is_phi, numeric_result, phi_apply, phi_from_leaves
from luau_recover.pipeline import Pipeline
from luau_recover.model import Node


class PhiUnitTests(unittest.TestCase):
    def test_phi_collapse_equal_components(self):
        result = phi_apply(lambda a, b: a + b, Phi(3, 3), 4)
        self.assertEqual(result, 7)

    def test_phi_stays_phi_when_components_differ(self):
        result = phi_apply(lambda a, b: a * b, Phi(3, 5), 2)
        self.assertIsInstance(result, Phi)
        self.assertEqual(result.values, (6, 10))

    def test_phi_comparison_collapses(self):
        from luau_recover.passes import phi_binop
        # Mixed comparison result -> UNKNOWN (both branches possible)
        self.assertIs(phi_binop("==", Phi(10, 20), 10), UNKNOWN)
        # Same verdict for both components -> scalar bool
        self.assertIs(phi_binop(">", Phi(30, 40), 10), True)
        self.assertIs(phi_binop("==", Phi(10, 10), 10), True)

    def test_float_mod_matches_lua(self):
        # Lua: -5 % 3 == 1, 5 % -3 == -1 (floor semantics, not fmod)
        self.assertEqual(numeric_result("%", -5.0, 3.0), 1.0)
        self.assertEqual(numeric_result("%", 5.0, -3.0), -1.0)
        self.assertEqual(numeric_result("%", -5, 3), 1)


class PhiEvaluateTests(unittest.TestCase):
    def parse(self, source):
        root, errors, _ = parse(source)
        self.assertEqual(errors, [])
        return root

    def test_ifexpr_unknown_cond_yields_phi(self):
        source = "local c = f()\nlocal x = if c then 3945 else 6118\nprint(x)\n"
        root = self.parse(source)
        analyzer = analyze(root)
        print_node = root.fields["body"][2].get("values", [None])[0]
        from luau_recover.passes import binding_for
        # evaluate print(x) arg: x unknown -> env empty; build env by hand
        x_name = print_node
        # simpler: evaluate the ifexpr node directly from statement 1
        ifexpr = root.fields["body"][1].get("values", [])[0]
        value = evaluate(ifexpr, {}, analyzer)
        self.assertIsInstance(value, Phi)
        self.assertEqual(value.values, (3945, 6118))

    def test_ifexpr_same_leaves_collapse(self):
        source = "local c = f()\nlocal x = if c then 5 else 5\n"
        root = self.parse(source)
        analyzer = analyze(root)
        ifexpr = root.fields["body"][1].get("values", [])[0]
        self.assertEqual(evaluate(ifexpr, {}, analyzer), 5)


class NestedPoolTests(unittest.TestCase):
    def parse(self, source):
        root, errors, _ = parse(source)
        self.assertEqual(errors, [])
        return root

    def resolve(self, source):
        root = self.parse(source)
        stats = PassStats()
        resolver = PoolResolver(root, analyze(root), source, allow_escape=True)
        resolver.apply(root, stats)
        return root, stats, resolver

    def test_nested_table_read_resolves(self):
        source = 'local P = {}\nP[140] = {10, 20, 30}\nlocal a = P[140][2]\nprint(a)\n'
        root, stats, _ = self.resolve(source)
        output = emit(root, source)
        self.assertIn("local a = 20", output)

    def test_alias_of_nested_table_read_resolves(self):
        source = 'local P = {}\nP[140] = {10, 20, 30}\nlocal A = P[140]\nlocal a = A[3]\nprint(a)\n'
        root, stats, _ = self.resolve(source)
        output = emit(root, source)
        self.assertIn("local a = 30", output)

    def test_nested_write_through_alias(self):
        source = 'local P = {}\nP[140] = {10, 20, 30}\nlocal A = P[140]\nA[1] = 99\nlocal a = P[140][1]\nlocal b = A[2]\nprint(a, b)\n'
        root, stats, _ = self.resolve(source)
        output = emit(root, source)
        self.assertIn("local a = 99", output)
        self.assertIn("local b = 20", output)

    def test_nested_write_does_not_corrupt_top_level(self):
        source = 'local P = {}\nP[140] = {10, 20}\nP[7] = 5\nlocal A = P[140]\nA[1] = 99\nlocal top = P[7]\nlocal nest = P[140][1]\nprint(top, nest)\n'
        root, stats, _ = self.resolve(source)
        output = emit(root, source)
        self.assertIn("local top = 5", output)
        self.assertIn("local nest = 99", output)

    def test_alias_unstable_after_slot_rewrite(self):
        source = 'local P = {}\nP[140] = {10, 20}\nlocal A = P[140]\nP[140] = {70, 80}\nlocal a = A[1]\nlocal b = P[140][1]\nprint(a, b)\n'
        root, stats, _ = self.resolve(source)
        output = emit(root, source)
        # A captured the OLD table; the resolver must not rewrite A[1] = 70
        self.assertIn("local a = A[1]", output)
        self.assertIn("local b = 70", output)

    def test_multi_assign_pool_init(self):
        source = 'local P = {}\nP[1], P[2] = 11, 22\nlocal a = P[1]\nlocal b = P[2]\nprint(a, b)\n'
        root, stats, _ = self.resolve(source)
        output = emit(root, source)
        self.assertIn("local a = 11", output)
        self.assertIn("local b = 22", output)

    def test_multi_assign_swap_keeps_semantics(self):
        source = 'local P = {}\nP[1], P[2] = 11, 22\nP[1], P[2] = P[2], P[1]\nlocal a = P[1]\nlocal b = P[2]\nprint(a, b)\n'
        root, stats, _ = self.resolve(source)
        output = emit(root, source)
        self.assertIn("local a = 22", output)
        self.assertIn("local b = 11", output)


class JunkPredicateRecoveryTests(unittest.TestCase):
    def parse(self, source):
        root, errors, _ = parse(source)
        self.assertEqual(errors, [])
        return root

    def run_pipeline(self, source):
        rendered, report = Pipeline().run(source)
        return rendered, report

    def test_opaque_predicate_dispatcher_recovers(self):
        # The exact luast junk shape: aux var gets `if c then k1 else k2`,
        # junk arithmetic over it, and the state transition is guarded by a
        # predicate that must hold for BOTH branches.
        # NOTE: the dispatcher uses a complement transform (s = 10 - s), so a
        # raw assignment of 2 lands on dispatch key 8 and vice versa. The
        # junk predicate (x % 1 == 0) is always true -> raw 2 -> key 8.
        source = (
            "local s = 0\n"
            "local c = f()\n"
            "while true do\n"
            " s = 10 - s\n"
            " if s == 10 then\n"
            "  local b = if c then 3 else 5\n"
            "  local z = 2 * b + 7 * (9 - b)\n"
            "  s = if z % 1 == 0 then 2 else 8\n"
            " elseif s == 2 then print(\"dead\") break\n"
            " elseif s == 8 then print(\"alive\") break\n"
            " end\n"
            "end\n"
        )
        rendered, report = self.run_pipeline(source)
        self.assertTrue(report.valid_output, report.fallback_reason)
        self.assertIn('print("alive")', rendered)
        self.assertNotIn('print("dead")', rendered)
        self.assertNotIn("while true", rendered)
        self.assertNotIn("% 1 == 0", rendered)

    def test_junk_predicate_does_not_duplicate_paths(self):
        # Without Phi the junk fork doubles the emitted payload per state and
        # keeps the (unreachable) other branch alive. With Phi the always-true
        # predicate collapses, the unreachable state vanishes, and the payload
        # is emitted exactly once.
        payload = 'print("PAYLOAD")'
        source = (
            "local s = 0\n"
            "local c = f()\n"
            "while true do\n"
            " s = 4 - s\n"
            " if s == 4 then\n"
            "  local b = if c then 1 else 2\n"
            "  local z = 3 * b\n"
            "  s = if z % 1 == 0 then 2 else 100\n"
            " elseif s == 2 then " + payload + " break\n"
            " elseif s == 100 then print(\"UNREACHABLE\") break\n"
            " end\n"
            "end\n"
        )
        rendered, report = self.run_pipeline(source)
        self.assertTrue(report.valid_output, report.fallback_reason)
        self.assertEqual(rendered.count(payload), 1)
        self.assertNotIn("UNREACHABLE", rendered)
        self.assertNotIn("while true", rendered)
        self.assertNotIn("% 1 == 0", rendered)


if __name__ == "__main__":
    unittest.main()
