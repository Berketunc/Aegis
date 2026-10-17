"""Truth table for CLMIPOL. The same cases run against the Python twin and, if cobc exists, the COBOL binary."""
import subprocess

import pytest

from txn.gateway.policy import PolicyInput, encode, evaluate, evaluate_cobol, evaluate_python, parse_output

R, A = 0.85, 0.60
CASES = [
    # (score, dup, swtag, risk_ok[, lowres]) -> (band, reasons)
    ((0.10, 0, 0, 1), ("GREEN", ["NONE"])),
    ((0.00, 0, 0, 1), ("GREEN", ["NONE"])),
    ((0.5999, 0, 0, 1), ("GREEN", ["NONE"])),
    ((0.60, 0, 0, 1), ("AMBER", ["AI_SCORE_ABOVE_AMBER"])),
    ((0.70, 0, 0, 1), ("AMBER", ["AI_SCORE_ABOVE_AMBER"])),
    ((0.8499, 0, 0, 1), ("AMBER", ["AI_SCORE_ABOVE_AMBER"])),
    ((0.85, 0, 0, 1), ("RED", ["AI_SCORE_ABOVE_RED"])),
    ((1.00, 0, 0, 1), ("RED", ["AI_SCORE_ABOVE_RED"])),
    ((0.10, 1, 0, 1), ("RED", ["DUPLICATE_PHOTO"])),
    ((0.95, 1, 0, 1), ("RED", ["DUPLICATE_PHOTO", "AI_SCORE_ABOVE_RED"])),
    ((0.70, 1, 0, 1), ("RED", ["DUPLICATE_PHOTO", "AI_SCORE_ABOVE_AMBER"])),
    ((0.10, 0, 1, 1), ("AMBER", ["EDITING_SOFTWARE_TAG"])),
    ((0.70, 0, 1, 1), ("AMBER", ["AI_SCORE_ABOVE_AMBER", "EDITING_SOFTWARE_TAG"])),
    ((0.90, 0, 1, 1), ("RED", ["AI_SCORE_ABOVE_RED", "EDITING_SOFTWARE_TAG"])),
    ((0.90, 1, 1, 1), ("RED", ["DUPLICATE_PHOTO", "AI_SCORE_ABOVE_RED", "EDITING_SOFTWARE_TAG"])),
    # Fail-safe: risk service down is AMBER regardless of anything else, never GREEN.
    ((0.00, 0, 0, 0), ("AMBER", ["RISK_SERVICE_UNAVAILABLE"])),
    ((0.99, 1, 1, 0), ("AMBER", ["RISK_SERVICE_UNAVAILABLE"])),
    # Low-resolution photo: the AI score alone can hold the claim for review, never refer it.
    ((0.90, 0, 0, 1, 1), ("AMBER", ["AI_SCORE_ABOVE_RED", "LOW_RESOLUTION_EVIDENCE"])),
    ((1.00, 0, 1, 1, 1), ("AMBER", ["AI_SCORE_ABOVE_RED", "LOW_RESOLUTION_EVIDENCE", "EDITING_SOFTWARE_TAG"])),
    ((0.90, 1, 0, 1, 1), ("RED", ["DUPLICATE_PHOTO", "AI_SCORE_ABOVE_RED", "LOW_RESOLUTION_EVIDENCE"])),
    ((0.70, 0, 0, 1, 1), ("AMBER", ["AI_SCORE_ABOVE_AMBER"])),
    ((0.10, 0, 0, 1, 1), ("GREEN", ["NONE"])),
    ((0.90, 0, 0, 0, 1), ("AMBER", ["RISK_SERVICE_UNAVAILABLE"])),
    # Out-of-range scores are clamped, not trusted.
    ((1.70, 0, 0, 1), ("RED", ["AI_SCORE_ABOVE_RED"])),
    ((-0.2, 0, 0, 1), ("GREEN", ["NONE"])),
]


def _pin(score, dup, sw, ok, lowres=0):
    return PolicyInput(score, R, A, bool(dup), bool(sw), bool(ok), bool(lowres))


@pytest.mark.parametrize("args,expected", CASES)
def test_python_twin(args, expected):
    assert evaluate_python(_pin(*args)) == expected


def test_encode_is_fixed_width():
    line = encode(_pin(0.91, 0, 0, 1))
    assert line == "09100 08500 06000 0 0 1 0"
    assert len(line) == 25
    assert encode(_pin(0.91, 0, 0, 1, 1)).endswith(" 1 1")


def test_parse_output_rejects_garbage():
    for bad in ["", "BLUE NONE", "RED", "GREEN "]:
        with pytest.raises(ValueError):
            parse_output(bad)


def test_engine_failure_is_amber(tmp_path):
    broken = tmp_path / "CLMIPOL"
    broken.write_text("not a binary")
    broken.chmod(0o755)
    res = evaluate(_pin(0.0, 0, 0, 1), engine="cobol", binary=str(broken))
    assert (res.band, res.reasons) == ("AMBER", ["POLICY_ENGINE_ERROR"])
    res = evaluate(_pin(0.0, 0, 0, 1), engine="cobol", binary=str(tmp_path / "missing"))
    assert res.band == "AMBER"


def test_auto_falls_back_to_python(tmp_path):
    res = evaluate(_pin(0.9, 0, 0, 1), engine="auto", binary=str(tmp_path / "missing"))
    assert (res.band, res.engine) == ("RED", "python")


@pytest.mark.parametrize("args,expected", CASES)
def test_cobol_matches_python(cobol_bin, args, expected):
    assert evaluate_cobol(_pin(*args), cobol_bin) == expected


def test_cobol_rejects_bad_input(cobol_bin):
    proc = subprocess.run([cobol_bin], input="garbage\n", capture_output=True, text=True, timeout=2)
    assert proc.stdout.strip() == "AMBER POLICY_INPUT_INVALID"
    # The pre-low-resolution 23-column format is rejected too, never silently defaulted.
    proc = subprocess.run([cobol_bin], input="09100 08500 06000 0 0 1\n", capture_output=True, text=True, timeout=2)
    assert proc.stdout.strip() == "AMBER POLICY_INPUT_INVALID"
