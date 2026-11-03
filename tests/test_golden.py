"""Pass/fail rules shared by every sim adapter (svkit.core.golden.evaluate)."""
from svkit.config import Golden
from svkit.core import golden as G


def _files(tmp_path, actual, expected):
    a, e = tmp_path / "t.out", tmp_path / "t.out.correct"
    a.write_text(actual)
    e.write_text(expected)
    return a, e


def test_nonzero_exit_with_golden_mismatch_reports_divergence(tmp_path):
    # `make t.check` exits non-zero because its diff failed: say where, not just "exit code 2"
    a, e = _files(tmp_path, "1\n2\n9\n", "1\n2\n3\n")
    passed, reason, div = G.evaluate(exit_code=2, log_text="", pass_regex=None, fail_regex=None,
                                     actual=a, expected=e, golden=Golden(actual="t.out", expected="t.out.correct"))
    assert passed is False and reason == "golden mismatch at line 3 (exit code 2)"
    assert (div.line, div.expected, div.actual) == (3, "3", "9")


def test_nonzero_exit_with_matching_golden_still_fails(tmp_path):
    a, e = _files(tmp_path, "1\n", "1\n")
    passed, reason, div = G.evaluate(exit_code=2, log_text="", pass_regex=None, fail_regex=None,
                                     actual=a, expected=e, golden=Golden(actual="t.out", expected="t.out.correct"))
    assert (passed, reason, div) == (False, "exit code 2", None)
