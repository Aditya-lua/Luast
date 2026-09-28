import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from luau_recover.analysis import analyze
from luau_recover.emitter import emit
from luau_recover.parser import parse
from luau_recover.passes import PassStats, PoolResolver, fold_constants
from luau_recover.environment import EnvironmentReport, RobloxEnvironment
from luau_recover.pipeline import Pipeline


class RecoveryTests(unittest.TestCase):
    def parse(self, source):
        root, errors, _ = parse(source)
        self.assertEqual(errors, [])
        return root

    def test_round_trip_luau_features(self):
        source = "local x: number = 1\nlocal f = function(a: string, ...): number return #a end\nprint(if x == 1 then `a` else \"b\")\nprint((\"value\"):format(\"%s\", \"ok\"))\ntype Handler = (number, string) -> boolean\n"
        root = self.parse(source)
        output = emit(root, source)
        self.assertEqual(parse(output)[1], [])
        self.assertNotIn(":: :", output)
        self.assertNotIn(":: ::", output)

    def test_pool_and_fold(self):
        source = 'local P = {"hello", 2}; local a = P[1]; local b = (5 * 2) - 3; print(a, b)\n'
        root = self.parse(source)
        stats = PassStats()
        PoolResolver(root, analyze(root), source).apply(root, stats)
        fold_constants(root, analyze(root), stats)
        output = emit(root, source)
        self.assertIn('local a = "hello"', output)
        self.assertIn("local b = 7", output)
        self.assertEqual(parse(output)[1], [])

    def test_comparison_type_mismatch_is_not_folded(self):
        source = "local x = 1 < \"2\"\n"
        root = self.parse(source)
        stats = PassStats()
        fold_constants(root, analyze(root), stats)
        self.assertIn("1 < \"2\"", emit(root, source))

    def test_dispatcher_recovery(self):
        source = "local s = 0\nwhile true do\n s = 10 - s\n if s == 10 then print(\"a\") s = 8 elseif s == 2 then print(\"b\") break end\nend\n"
        root = self.parse(source)
        from luau_recover.controlflow import DispatcherPass
        stats = PassStats()
        DispatcherPass(root, analyze(root), source, stats).apply()
        output = emit(root, source)
        self.assertNotIn("while true", output)
        self.assertIn('print("a")', output)
        self.assertIn('print("b")', output)
        self.assertEqual(parse(output)[1], [])

    def test_pipeline_reports_metrics(self):
        source = 'local P = {"x"}; print(P[1])\n'
        output, report = Pipeline().run(source)
        self.assertTrue(report.valid_output)
        self.assertEqual(report.input_bytes, len(source.encode()))
        self.assertGreaterEqual(report.output_tokens, 1)
        self.assertIn("x", output)

    def test_numeric_for_rename(self):
        source = "for i = 1, 3 do print(i) end\n"
        output, report = Pipeline().run(source)
        self.assertTrue(report.valid_output)
        self.assertEqual(parse(output)[1], [])
        self.assertTrue(any(item["name"] == "rename" and item["status"] == "committed" for item in report.history))


    def test_environment_trace_parser(self):
        environment = RobloxEnvironment(Path("/missing"))
        raw = "\x00HB\n" + (" " * 16384) + "\n\x00ENVLOG-BEGIN\n-- run status: finished\n-- 12 statements recorded in 0.01s\n-- non-standard globals touched: foo, bar\n\x00ENVLOG-END"
        trace = environment.extract_trace(raw)
        report = EnvironmentReport()
        environment.parse_metadata(trace, report)
        self.assertIn("12 statements recorded", trace)
        self.assertEqual(report.statements, 12)
        self.assertEqual(report.globals_touched, ["foo", "bar"])
        self.assertEqual(report.status, "finished")


if __name__ == "__main__":
    unittest.main()
