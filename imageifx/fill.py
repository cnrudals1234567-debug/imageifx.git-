"""LaMa(CPU, 무료) 기반 인페인트/아웃페인트."""
import numpy as np
from PIL import Image

_lama = None


def _get():
    global _lama
    if _lama is None:
        import torch
        # LaMa 가중치가 CUDA 텐서로 저장돼 있어 CPU 전용 PC에서는 map_location 지정이 필요
        _orig = torch.jit.load
        torch.jit.load = lambda p, *a, **k: _orig(p, map_location="cpu")
        try:
            from simple_lama_inpainting import SimpleLama
            _lama = SimpleLama(device=torch.device("cpu"))
        finally:
            torch.jit.load = _orig
    return _lama


def inpaint(img: Image.Image, mask: np.ndarray) -> Image.Image:
    """mask: bool/uint8 배열(True=채울 영역). 마스크 밖 픽셀은 원본 그대로 유지."""
    m = (mask > 0).astype(np.uint8) * 255
    if not m.any():
        return img.convert("RGB")
    out = _get()(img.convert("RGB"), Image.fromarray(m))
    out = out.convert("RGB").crop((0, 0, img.width, img.height))
    src = np.asarray(img.convert("RGB"))
    res = np.asarray(out).copy()
    keep = m == 0
    res[keep] = src[keep]
    return Image.fromarray(res)
