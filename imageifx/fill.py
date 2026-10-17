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


def outpaint_progressive(canvas: Image.Image, covered: np.ndarray, step=96, window=512, prefilled=None) -> Image.Image:
    """좌우 가장자리 확장용 단계적 LaMa. 한 번에 수백 px를 채우면 모델이 맥락을 잃어 번지므로,
    경계에서 step px씩 바깥으로 나가며, 매번 경계 주변 window px만 잘라 채우고 새 띠만 붙인다.
    covered: 원본이 있는 곳(True). 가운데 열 범위만 있고 위/아래가 비면 마지막에 전체 한 번 더 채운다."""
    a = np.asarray(canvas.convert("RGB")).copy()
    H, W = a.shape[:2]
    cols = covered.any(axis=0)
    c0, c1 = int(cols.argmax()), W - int(cols[::-1].argmax())      # 채워진 열 범위 [c0, c1)
    done = covered.copy() if prefilled is None else (covered | prefilled)   # prefilled: 이미 다른 방법으로 채운 곳(AI가 건드리지 않고 맥락으로만 씀)

    def run(x0, x1, new0, new1):
        m = np.zeros((H, x1 - x0), np.uint8)
        m[:, new0 - x0:new1 - x0] = 1
        m &= (~done[:, x0:x1]).astype(np.uint8)                     # 이미 있는 픽셀은 건드리지 않는다
        if not m.any():
            return
        out = np.asarray(inpaint(Image.fromarray(a[:, x0:x1]), m))
        keep = m > 0
        a[:, x0:x1][keep] = out[keep]
        done[:, x0:x1][keep] = True

    inner = ~done[:, c0:c1]
    if inner.any():                                                # 원본이 있는 열의 위/아래 빈 띠를 먼저 채운다(옆 확장의 맥락이 검은색이 되지 않게)
        out = np.asarray(inpaint(Image.fromarray(a[:, c0:c1]), inner))
        a[:, c0:c1][inner] = out[inner]
        done[:, c0:c1] = True
    x = c1
    while x < W:                                                   # 오른쪽으로
        nx = min(W, x + step)
        run(max(0, nx - window), nx, x, nx)
        x = nx
    x = c0
    while x > 0:                                                   # 왼쪽으로
        nx = max(0, x - step)
        run(nx, min(W, nx + window), nx, x)
        x = nx
    if not done.all():                                             # 위/아래 남은 곳
        return inpaint(Image.fromarray(a), ~done)
    return Image.fromarray(a)


def mirror_right(canvas: Image.Image, covered: np.ndarray, y_from: int, x_min: int = 0):
    """오른쪽 확장의 y_from 아래 행을 원본 오른쪽 가장자리 기준 좌우 반사로 채운다. 실제 원본 픽셀이라 AI 확장처럼 흐려지지 않고,
    가장자리에서 픽셀이 이어진다. 글자가 없는 풍경 구간에만 쓴다. x_min: 반사에 쓸 원본의 가장 왼쪽 열(그 왼쪽에 인물 손 등이 있으면 제외).
    확장 폭이 그보다 넓으면 같은 구간을 왕복(반사 반복)한다. (채운 canvas, 채운 곳 mask) 반환."""
    a = np.asarray(canvas.convert("RGB")).copy()
    H, W = a.shape[:2]
    cols = covered.any(axis=0)
    c0, c1 = int(cols.argmax()), W - int(cols[::-1].argmax())
    filled = np.zeros((H, W), bool)
    y0 = max(0, y_from)
    lo = max(c0, x_min)
    m = c1 - lo                                     # 반사에 쓸 폭
    if m <= 0 or c1 >= W or y0 >= H:
        return canvas, filled
    k = np.arange(W - c1)                           # 확장 열 번호
    ph = k % (2 * m)
    src = np.where(ph < m, c1 - 1 - ph, c1 - m + (ph - m))   # 앞 구간은 반사, 다음 구간은 정방향(왕복)
    a[y0:, c1:] = a[y0:, src]
    filled[y0:, c1:] = True
    return Image.fromarray(a), filled
