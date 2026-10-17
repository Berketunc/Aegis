"""Export the trained detector to ONNX for zDLC, plus parity crops for LinuxONE (plan.md §7.7, §8.3).

Static input [3,3,224,224] (one claim photo = 3 crops), opset 17, output [3,1] raw logits.
The risk service averages the 3 logits, divides by the temperature, and applies sigmoid.

Also writes parity sets: uint8 crops and Modal FP32 scores for a sample of val and test
images, so B can run the compiled model on LinuxONE and A can fit thresholds on the
LinuxONE scores.

Outputs on the volume under /data/models/<run>/: model.onnx, parity_val.npz, parity_test.npz,
export.json. Local copies in models/<run>/ (gitignored).

Usage:
    modal run train/export_onnx.py --run det-r18-v1 --n-parity 300
"""

import json
from pathlib import Path

import modal

VOL = modal.Volume.from_name("claimshield-data")
MNT = "/vol"

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install("torch==2.4.1", "torchvision==0.19.1", index_url="https://download.pytorch.org/whl/cpu")
    .pip_install("onnx", "onnxruntime", "pillow", "numpy", "scikit-learn")
    .add_local_python_source("train_modal")
)
app = modal.App("claimshield-export", image=image)


@app.function(volumes={MNT: VOL}, timeout=1800, cpu=4, memory=16384)
def export(run: str, n_parity: int = 300) -> dict:
    import numpy as np
    import onnx
    import onnxruntime as ort
    import torch
    import torch.nn as nn
    from PIL import Image
    from torchvision import models

    from train_modal import CROP, MEAN, STD, no_upscale_of, read_manifest, three_crops

    root = Path(MNT)
    d = root / "data/models" / run
    model = models.resnet18()
    model.fc = nn.Linear(512, 1)
    model.load_state_dict(torch.load(d / "best.pt", map_location="cpu"))
    model.eval()

    x = torch.randn(3, 3, CROP, CROP)
    onnx_path = d / "model.onnx"
    torch.onnx.export(
        model, x, str(onnx_path), opset_version=17, input_names=["crops"], output_names=["logits"],
        dynamic_axes=None, do_constant_folding=True,
    )
    onnx.checker.check_model(str(onnx_path))
    sess = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])

    mean = np.array(MEAN, np.float32)[:, None, None]
    std = np.array(STD, np.float32)[:, None, None]
    temperature = json.loads((d / "temperature.json").read_text())["temperature"]
    NO_UP = no_upscale_of(d)

    def prep(path):
        im = Image.open(root / path).convert("RGB")
        crops = np.stack([np.asarray(c, np.uint8) for c in three_crops(im, NO_UP)])  # [3,224,224,3] uint8
        return crops

    def to_tensor(crops_u8):
        f = crops_u8.astype(np.float32).transpose(0, 3, 1, 2) / 255.0
        return (f - mean) / std

    info = {"run": run, "onnx": str(onnx_path), "opset": 17, "input": "crops [3,3,224,224] float32, ImageNet-normalised",
            "output": "logits [3,1]; score = sigmoid(mean(logits)/temperature)", "temperature": temperature,
            "preprocess": {"short_side": 640, "no_upscale": NO_UP, "crop": CROP}}
    max_diff = 0.0
    for split in ("val", "test"):
        rows = read_manifest(root, split)
        rows = sorted(rows, key=lambda r: r["id"])
        import random

        random.Random(1).shuffle(rows)
        rows = rows[:n_parity]
        crops, logits_t, logits_o, labels, ids = [], [], [], [], []
        for r in rows:
            c = prep(r["out"])
            xt = torch.from_numpy(to_tensor(c))
            with torch.no_grad():
                lt = model(xt).numpy()
            lo = sess.run(None, {"crops": to_tensor(c)})[0]
            max_diff = max(max_diff, float(np.abs(lt - lo).max()))
            crops.append(c)
            logits_t.append(lt.squeeze(1))
            logits_o.append(lo.squeeze(1))
            labels.append(1 if r["label"] == "fake" else 0)
            ids.append(r["id"])
        np.savez_compressed(
            d / f"parity_{split}.npz", crops=np.stack(crops), logits_torch=np.stack(logits_t), logits_onnx=np.stack(logits_o),
            labels=np.array(labels), ids=np.array(ids), mean=mean.squeeze(), std=std.squeeze(), temperature=temperature,
        )
        info[f"parity_{split}_n"] = len(rows)
    info["max_abs_logit_diff_torch_vs_onnxruntime"] = max_diff
    info["onnx_bytes"] = onnx_path.stat().st_size
    (d / "export.json").write_text(json.dumps(info, indent=2))
    VOL.commit()
    return info


@app.local_entrypoint()
def main(run: str = "det-r18-v1", n_parity: int = 300):
    import subprocess

    info = export.remote(run, n_parity)
    print(json.dumps(info, indent=2))
    local = Path("models") / run
    local.mkdir(parents=True, exist_ok=True)
    for f in ("model.onnx", "parity_val.npz", "parity_test.npz", "export.json", "temperature.json"):
        subprocess.run(["modal", "volume", "get", "--force", "claimshield-data", f"/data/models/{run}/{f}", str(local / f)], check=True)
    print(f"local copies in {local}/ (gitignored). Hand model.onnx + parity_*.npz + export.json to B.")
