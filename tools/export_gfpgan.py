"""Convert the official GFPGAN v1.4 weights (TencentARC, Apache-2.0) to ONNX for FaceSort.
Input 1x3x512x512 RGB in [-1, 1]; output the same."""
import os
import sys
import types

HERE = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")      # holds src/ (GFPGAN) and GFPGANv1.4.pth
sys.path.insert(0, os.path.join(HERE, "src"))

# GFPGAN's model code only needs two small helpers from basicsr (which won't build here)
reg = types.ModuleType("basicsr.utils.registry")


class _Reg:
    def register(self, *a, **k):
        return (lambda c: c) if not a or not callable(a[0]) else a[0]


reg.ARCH_REGISTRY = _Reg()
au = types.ModuleType("basicsr.archs.arch_util")


def default_init_weights(module_list, scale=1, bias_fill=0, **kwargs):
    pass


au.default_init_weights = default_init_weights
for name in ("basicsr", "basicsr.utils", "basicsr.archs"):
    sys.modules[name] = types.ModuleType(name)
sys.modules["basicsr.utils.registry"] = reg
sys.modules["basicsr.archs.arch_util"] = au
pkg = types.ModuleType("gfpgan")
pkg.__path__ = [os.path.join(HERE, "src", "gfpgan")]
sys.modules["gfpgan"] = pkg
archs = types.ModuleType("gfpgan.archs")
archs.__path__ = [os.path.join(HERE, "src", "gfpgan", "archs")]
sys.modules["gfpgan.archs"] = archs

import torch  # noqa: E402
from gfpgan.archs.gfpganv1_clean_arch import GFPGANv1Clean  # noqa: E402

net = GFPGANv1Clean(out_size=512, num_style_feat=512, channel_multiplier=2, decoder_load_path=None,
                    fix_decoder=False, num_mlp=8, input_is_latent=True, different_w=True, narrow=1,
                    sft_half=True)
ck = torch.load(os.path.join(HERE, "GFPGANv1.4.pth"), map_location="cpu", weights_only=True)
net.load_state_dict(ck["params_ema"] if "params_ema" in ck else ck["params"], strict=True)
net.eval()


class Wrap(torch.nn.Module):
    def __init__(self, n):
        super().__init__()
        self.n = n

    def forward(self, x):
        return self.n(x, return_rgb=False, randomize_noise=False)[0]


w = Wrap(net).eval()
x = torch.rand(1, 3, 512, 512) * 2 - 1
with torch.no_grad():
    ref = w(x)
out = os.path.join(HERE, "gfpgan_1.4.onnx")
torch.onnx.export(w, x, out, input_names=["input"], output_names=["output"], opset_version=17,
                  do_constant_folding=True, dynamo=False)
import numpy as np  # noqa: E402
import onnxruntime as ort  # noqa: E402
s = ort.InferenceSession(out, providers=["CPUExecutionProvider"])
got = s.run(None, {"input": x.numpy()})[0]
print("max diff vs torch:", float(np.abs(got - ref.numpy()).max()), "size MB", os.path.getsize(out) / 1e6)
