"""Offline tests for actual response accounting methods, without live imports."""
import ast
import pathlib
import unittest
from typing import Any

SOURCE = pathlib.Path(__file__).resolve().parents[1] / "src/assistx/fleet_executor.py"
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
klass = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "FleetExecutor")
methods = [n for n in klass.body if isinstance(n, ast.FunctionDef) and
           n.name in {"_router_accounting", "_result_from_router"}]
assert len(methods) == 2
fixture = ast.Module(body=[ast.ClassDef(name="FleetExecutor", bases=[], keywords=[],
    body=methods, decorator_list=[], type_params=[])], type_ignores=[])
ns = {"Any": Any}
exec(compile(ast.fix_missing_locations(fixture), str(SOURCE), "exec"), ns)
Executor = ns["FleetExecutor"]


class AccountingTests(unittest.TestCase):
    def result(self, usage=None, finish="stop", content="answer", status=200):
        body={"choices":[{"message":{"content":content},"finish_reason":finish}]}
        if usage is not None:
            body["usage"]=usage
        state, result=Executor._result_from_router({"status_code":status,"body":body})
        return state,result["response_accounting"]

    def test_reconciled_counts_and_no_productivity_claim(self):
        state,a=self.result({"prompt_tokens":12,"completion_tokens":3,"total_tokens":15})
        self.assertEqual(state,"DONE")
        self.assertEqual(a["usage_state"],"COMPLETE_REPORTED")
        self.assertEqual(a["reconciled_reported_total_tokens"],15)
        self.assertFalse(a["provider_execution_verified"])
        self.assertFalse(a["independent_quality_verified"])
        self.assertFalse(a["billing_verified"])
        self.assertIsNone(a["accepted_work_units"])

    def test_zero_valid_and_derived_total(self):
        _,a=self.result({"prompt_tokens":0,"completion_tokens":0})
        self.assertEqual(a["usage_state"],"COMPLETE_REPORTED")
        self.assertEqual(a["reconciled_reported_total_tokens"],0)
        self.assertIsNone(a["reported_total_tokens"])

    def test_missing_and_partial_never_zero_filled(self):
        for usage,state in ((None,"NOT_REPORTED"),({},"NOT_REPORTED"),
                            ({"completion_tokens":4},"PARTIAL_REPORTED"),
                            ({"total_tokens":4},"PARTIAL_REPORTED")):
            with self.subTest(usage=usage):
                _,a=self.result(usage)
                self.assertEqual(a["usage_state"],state)
                self.assertIsNone(a["reconciled_reported_total_tokens"])
                self.assertIsNone(a["reported_prompt_tokens"])

    def test_bad_types_and_ranges(self):
        for value in (True,False,1.0,"1",-1,[],{},1<<63):
            with self.subTest(value=value):
                _,a=self.result({"prompt_tokens":value,"completion_tokens":2})
                self.assertEqual(a["usage_state"],"INVALID")
                self.assertIsNone(a["reported_prompt_tokens"])
                self.assertIsNone(a["reconciled_reported_total_tokens"])

    def test_non_object_usage_and_mismatch(self):
        for usage in ("tokens",[],{"prompt_tokens":3,"completion_tokens":4,"total_tokens":8},
                      {"prompt_tokens":(1<<63)-1,"completion_tokens":1}):
            with self.subTest(usage=usage):
                _,a=self.result(usage)
                self.assertEqual(a["usage_state"],"INVALID")
                self.assertIsNone(a["reconciled_reported_total_tokens"])

    def test_finish_states_distinguish_truncation(self):
        for finish,expected in (("stop","TEXT_STOP_REPORTED"),("length","TEXT_TRUNCATED_REPORTED"),
                               (None,"TEXT_FINISH_UNKNOWN"),("tool_calls","TEXT_OTHER_FINISH")):
            with self.subTest(finish=finish):
                state,a=self.result(finish=finish)
                self.assertEqual(state,"DONE")  # compatibility: transport outcome unchanged
                self.assertEqual(a["response_state"],expected)
                self.assertIsNone(a["accepted_work_units"])

    def test_non_text_not_productive(self):
        for content in (None,"", "  ", {"text":"not a string"}, ["answer"]):
            with self.subTest(content=content):
                _,a=self.result(content=content)
                self.assertEqual(a["response_state"],"NO_NONEMPTY_TEXT")
                self.assertIsNone(a["accepted_work_units"])

    def test_http_error_usage_remains_unverified(self):
        state,a=self.result({"prompt_tokens":2,"completion_tokens":1},status=503)
        self.assertEqual(state,"FAILED")
        self.assertEqual(a["response_state"],"HTTP_NOT_SUCCESS")
        self.assertEqual(a["reconciled_reported_total_tokens"],3)
        self.assertFalse(a["provider_execution_verified"])

    def test_existing_result_fields_remain(self):
        response={"status_code":200,"body":{"choices":[{"message":{"content":"text"}}],
                  "model":"fixture","usage":{"completion_tokens":4}}}
        state,result=Executor._result_from_router(response)
        self.assertEqual(state,"DONE")
        self.assertEqual({k:v for k,v in result.items() if k!="response_accounting"},
                         {"exit_code":0,"status_code":200,"content":"text","model":"fixture",
                          "usage":{"completion_tokens":4}})

if __name__ == "__main__":
    unittest.main(verbosity=2)
