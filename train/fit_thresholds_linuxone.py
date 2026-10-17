"""Fit decision thresholds on LinuxONE scores (plan.md §7.7). Run locally; needs numpy only.

NNPA computes in DLFLOAT16, so the compiled model's scores drift slightly from Modal's FP32.
Thresholds are therefore fitted on what LinuxONE actually outputs.

Inputs
  models/<run>/parity_val.npz     from export_onnx.py: crops [N,3,224,224,3] uint8, logits_torch [N,3],
                                  labels [N], ids [N], mean, std, temperature
  <linuxone_val_logits.npy>       from B: the compiled model run on the same crops, shape [N,3]
                                  (same order as parity_val.npz; ImageNet-normalised float32 input)
  optional parity_test.npz + <linuxone_test_logits.npy> for a test-set parity report

Outputs
  eval/thresholds_<version>.json  read by the gateway and the risk service
  eval/parity_<version>.md        drift report (max/mean logit diff, score diff, AUROC on both sides)

Usage
  python train/fit_thresholds_linuxone.py --run det-r18-v1 --version t-1 \
      --val-logits path/to/linuxone_val_logits.npy [--test-logits path/to/linuxone_test_logits.npy] \
      [--build nnpa|cpu]
"""

import argparse
import json
from pathlib import Path

import numpy as np


def sigmoid(x):
    return 1 / (1 + np.exp(-x))


def auroc(scores, labels):
    scores, labels = np.asarray(scores, float), np.asarray(labels, int)
    order = np.argsort(scores, kind="mergesort")
    ranks = np.empty(len(scores))
    ranks[order] = np.arange(1, len(scores) + 1)
    # average ranks for ties
    for v in np.unique(scores):
        m = scores == v
        if m.sum() > 1:
            ranks[m] = ranks[m].mean()
    n1, n0 = labels.sum(), (1 - labels).sum()
    return float((ranks[labels == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def tpr_at_fpr(scores, labels, cap=0.01):
    scores, labels = np.asarray(scores), np.asarray(labels)
    thr = np.quantile(scores[labels == 0], 1 - cap)
    return float((scores[labels == 1] > thr).mean())


def compare(npz_path, lx_path, T):
    z = np.load(npz_path)
    ref = z["logits_torch"].astype(np.float64)  # [N,3]
    lx = np.load(lx_path).astype(np.float64).reshape(ref.shape)
    y = z["labels"].astype(int)
    p_ref = sigmoid(ref.mean(1) / T)
    p_lx = sigmoid(lx.mean(1) / T)
    return {
        "n": int(len(y)),
        "max_abs_logit_diff": float(np.abs(ref - lx).max()),
        "mean_abs_logit_diff": float(np.abs(ref - lx).mean()),
        "max_abs_score_diff": float(np.abs(p_ref - p_lx).max()),
        "auroc_modal_fp32": auroc(p_ref, y),
        "auroc_linuxone": auroc(p_lx, y),
        "tpr_at_1pct_fpr_modal": tpr_at_fpr(p_ref, y),
        "tpr_at_1pct_fpr_linuxone": tpr_at_fpr(p_lx, y),
    }, p_lx, y


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--version", required=True, help="thresholds version, e.g. t-1")
    ap.add_argument("--val-logits", required=True)
    ap.add_argument("--test-logits")
    ap.add_argument("--build", default="nnpa", choices=["nnpa", "cpu"])
    a = ap.parse_args()

    d = Path("models") / a.run
    T = json.loads((d / "temperature.json").read_text())["temperature"]
    val, p_val, y_val = compare(d / "parity_val.npz", a.val_logits, T)
    reals = np.sort(p_val[y_val == 0])
    tau_red = float(np.quantile(reals, 0.99))
    tau_amber = float(np.quantile(reals, 0.95))
    out = {
        "version": a.version,
        "model_version": a.run,
        "tau_red": tau_red,
        "tau_amber": tau_amber,
        "temperature": T,
        "score": "sigmoid(mean(3 crop logits) / temperature)",
        "fitted_on": f"LinuxONE {a.build.upper()} build, parity_val reals (n={int((y_val == 0).sum())})",
        "rule": "tau_red: real FPR <= 1% on val; tau_amber: real FPR <= 5% on val",
    }
    Path("eval").mkdir(exist_ok=True)
    Path(f"eval/thresholds_{a.version}.json").write_text(json.dumps(out, indent=2))

    lines = [f"# LinuxONE parity: `{a.run}` ({a.build.upper()} build)", "",
             f"Temperature {T:.4f}. Thresholds `{a.version}` fitted on LinuxONE scores: τ_red = {tau_red:.4f}, τ_amber = {tau_amber:.4f}.", "",
             "| Split | n | Max logit diff | Mean logit diff | Max score diff | AUROC Modal FP32 | AUROC LinuxONE | TPR@1%FPR Modal | TPR@1%FPR LinuxONE |",
             "|---|---|---|---|---|---|---|---|---|"]

    def row(name, r):
        return (f"| {name} | {r['n']} | {r['max_abs_logit_diff']:.4f} | {r['mean_abs_logit_diff']:.4f} | {r['max_abs_score_diff']:.4f} | "
                f"{r['auroc_modal_fp32']:.4f} | {r['auroc_linuxone']:.4f} | {r['tpr_at_1pct_fpr_modal']:.3f} | {r['tpr_at_1pct_fpr_linuxone']:.3f} |")

    lines.append(row("val", val))
    if a.test_logits:
        test, p_te, y_te = compare(d / "parity_test.npz", a.test_logits, T)
        lines.append(row("test", test))
        lines += ["", f"Test real FPR at τ_red on LinuxONE: {float((p_te[y_te == 0] >= tau_red).mean()):.3f}; "
                      f"at τ_amber: {float((p_te[y_te == 0] >= tau_amber).mean()):.3f}."]
    Path(f"eval/parity_{a.version}.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nwrote eval/thresholds_{a.version}.json")


if __name__ == "__main__":
    main()
