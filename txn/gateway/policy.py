"""Decision rule CLMIPOL: the COBOL program, plus a Python twin with the same truth table.

The gateway owns the decision; AI services only return signals (plan.md §6.1, §9.3).
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

BANDS = ("GREEN", "AMBER", "RED")
SEVERITY = {b: i for i, b in enumerate(BANDS)}


@dataclass(frozen=True)
class PolicyInput:
    ai_score: float          # calibrated probability, 0..1
    tau_red: float
    tau_amber: float
    duplicate: bool
    software_tag: bool
    risk_ok: bool


@dataclass(frozen=True)
class PolicyResult:
    band: str
    reasons: list[str]
    engine: str              # "cobol" | "python"


def _fixed(x: float) -> int:
    return max(0, min(10000, round(x * 10000)))


def encode(p: PolicyInput) -> str:
    """Fixed-width line read by CLMIPOL.cbl: 'SSSSS RRRRR AAAAA D S K'."""
    return (f"{_fixed(p.ai_score):05d} {_fixed(p.tau_red):05d} {_fixed(p.tau_amber):05d} "
            f"{int(p.duplicate)} {int(p.software_tag)} {int(p.risk_ok)}")


def parse_output(line: str) -> tuple[str, list[str]]:
    parts = line.strip().split(" ", 1)
    if len(parts) != 2 or parts[0] not in BANDS or not parts[1]:
        raise ValueError(f"unparseable CLMIPOL output: {line!r}")
    return parts[0], parts[1].split(",")


def evaluate_python(p: PolicyInput) -> tuple[str, list[str]]:
    """Python twin of CLMIPOL.cbl. Compares the same x10000 integers so both engines agree exactly."""
    score, red, amber = _fixed(p.ai_score), _fixed(p.tau_red), _fixed(p.tau_amber)
    reasons: list[tuple[str, str]] = []
    if not p.risk_ok:
        reasons.append(("RISK_SERVICE_UNAVAILABLE", "AMBER"))
    else:
        if p.duplicate:
            reasons.append(("DUPLICATE_PHOTO", "RED"))
        if score >= red:
            reasons.append(("AI_SCORE_ABOVE_RED", "RED"))
        elif score >= amber:
            reasons.append(("AI_SCORE_ABOVE_AMBER", "AMBER"))
        if p.software_tag:
            reasons.append(("EDITING_SOFTWARE_TAG", "AMBER"))
    if not reasons:
        return "GREEN", ["NONE"]
    band = max((b for _, b in reasons), key=SEVERITY.__getitem__)
    return band, [r for r, _ in reasons]


def evaluate_cobol(p: PolicyInput, binary: str, timeout_s: float = 1.0) -> tuple[str, list[str]]:
    proc = subprocess.run([binary], input=encode(p) + "\n", capture_output=True, text=True,
                          timeout=timeout_s, check=True)
    return parse_output(proc.stdout)


def resolve_engine(engine: str, binary: str) -> str:
    if engine == "auto":
        return "cobol" if os.path.isfile(binary) and os.access(binary, os.X_OK) else "python"
    return engine


def evaluate(p: PolicyInput, engine: str = "auto", binary: str = "./CLMIPOL") -> PolicyResult:
    """Run the rule. Any engine failure fails safe to AMBER, never GREEN."""
    chosen = resolve_engine(engine, binary)
    if chosen == "python":
        band, reasons = evaluate_python(p)
        return PolicyResult(band, reasons, "python")
    try:
        band, reasons = evaluate_cobol(p, binary)
    except (OSError, subprocess.SubprocessError, ValueError):
        return PolicyResult("AMBER", ["POLICY_ENGINE_ERROR"], "cobol")
    return PolicyResult(band, reasons, "cobol")
