# zDLC: IBM Z Deep Learning Compiler builds

Plan.md section 8.3 compiles the detector's ONNX export to an s390x shared
library with the zDLC container, once for CPU and once with `--maccel=NNPA`, and
keeps the `--onnx-op-stats` output as evidence of which operations run on the
Telum accelerator.

## Status on 17 October 2026

**Not compiled.** Pulling `icr.io/ibmz/zdlc` on the team VM failed with an
authentication error, so the deployed detector path is the plan's documented
fallback: PyTorch for s390x from the event's Jupyter image, with `torch_nnpa` for
the accelerator (`linuxone/benchmark_resnet18.py`, `risk_service/detector.py`).
That fallback is measured in `eval/stock_resnet18.md`. The README and video must
say "PyTorch with the NNPA extension", not "zDLC compiled".

## Retry (one command on the VM)

```bash
docker logout icr.io                       # anonymous pulls of the public image normally work
python3 zdlc/export_stock_onnx.py           # stock ResNet-18 -> zdlc/build/resnet18-stock.onnx (sanity path)
bash zdlc/compile.sh zdlc/build/resnet18-stock.onnx resnet18-stock
# A's detector, once exported with static shape [3,3,224,224], opset 17:
bash zdlc/compile.sh .runtime/models/detector.onnx detector
```

`compile.sh` writes `zdlc/build/<stem>-cpu.so`, `zdlc/build/<stem>-nnpa.so`,
copies the ONNX-MLIR `PyRuntime` module out of the image, and saves the op
statistics to `zdlc/op_stats/`. Commit the op-stats text files; the `.so` files
are build outputs and are ignored by Git.

Check the current image tag and whether the flag is `--mcpu` or `--march` in
the upstream README before running: https://github.com/IBM/zDLC

## Not done if the pull keeps failing

Ask in the Discord IBM Z SME helpdesk channel for the current pull instructions.
Do not spend more than 30 minutes on it; the fallback already works and the
plan's risk table allows it, provided the submission says so plainly.
