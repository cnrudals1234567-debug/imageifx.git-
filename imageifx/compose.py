"""합성: 배경(필요시 LaMa 확장) + 원본 픽셀 레이어. 모든 좌표는 정수 px."""
import hashlib, json, os
import numpy as np
from PIL import Image, ImageDraw
from . import fill
from . import spec as _spec

S = _spec.get


def _scaled_size(w, h, s):
    return max(1, int(round(w * s))), max(1, int(round(h * s)))


def _extend_rows(canvas: Image.Image, rows) -> Image.Image:
    """rows: 채워진 행 마스크(True). 비어 있는 위/아래 띠를 가장자리 30행 평균색으로 채움.
    LaMa는 큰 빈 영역을 문맥 없이 채워 어두운 환각이 생길 수 있어, 밝고 평평한 가장자리에는 이 방식이 안전."""
    import cv2
    a = np.asarray(canvas).copy()
    first = int(rows.argmax())
    last = rows.size - 1 - int(rows[::-1].argmax())
    def band(lo, hi):
        m = a[lo:hi].astype(np.float32).mean(axis=0, keepdims=True)
        return cv2.GaussianBlur(m, (0, 0), sigmaX=S("compose.extend_rows.sigma_px"))  # 가로로만 완만하게
    if last < rows.size - 1:
        band_rows = S("compose.extend_rows.band_rows")
        a[last + 1:] = band(max(first, last - (band_rows - 1)), last + 1)
    if first > 0:
        a[:first] = band(first, min(last + 1, first + band_rows))
    return Image.fromarray(a)


def build_bg(clean: Image.Image, layout, cache_dir=None):
    W, H = layout["canvas"]
    b = layout["bg"]
    # 클린 배경 내용까지 키에 포함해야 분해 결과가 바뀌었을 때 낡은 캐시를 쓰지 않음
    h = hashlib.md5(json.dumps([W, H, b] + ([layout["band"]] if layout.get("band") else []), sort_keys=True).encode())
    h.update(clean.tobytes())
    key = h.hexdigest()[:10]
    cpath = os.path.join(cache_dir, f"bg_{key}.png") if cache_dir else None
    if cpath and os.path.exists(cpath):
        return Image.open(cpath).convert("RGB")
    bw, bh = _scaled_size(clean.width, clean.height, b["scale"])
    scaled = clean.resize((bw, bh), Image.LANCZOS)
    canvas = Image.new("RGB", (W, H), (0, 0, 0))
    canvas.paste(scaled, (b["x"], b["y"]))
    covered = np.zeros((H, W), bool)
    covered[max(0, b["y"]):min(H, b["y"] + bh), max(0, b["x"]):min(W, b["x"] + bw)] = True
    if not covered.all():
        rows = covered.all(axis=1)
        if covered.any(axis=0).all() and covered[rows].all() and (~rows).any() and rows.any() \
                and (rows[: rows.argmax()].sum() == 0 or rows[::-1][: rows[::-1].argmax()].sum() == 0) \
                and not (~rows[rows.argmax():rows.size - rows[::-1].argmax()]).any():
            canvas = _extend_rows(canvas, rows)   # 위/아래 띠만 비었을 때: 가장자리 색을 이어붙임
        else:
            canvas = fill.inpaint(canvas, ~covered)   # 좌우 등 복잡한 확장은 LaMa
    band = layout.get("band")
    if band:   # 하단 띠: 클린 배경의 띠 영역을 폭에 맞춰 캔버스 아래에 붙인다(글자는 이미 제거돼 있음)
        x0, y0, x1, y1 = band["src_bbox"]
        strip = clean.crop((x0, y0, x1, y1)).resize((W, band["h"]), Image.LANCZOS)
        canvas.paste(strip, (0, H - band["h"]))
    if cpath:
        canvas.save(cpath)
    return canvas


def _scrim(size, sc):
    """가장자리가 부드러운 흰 패널(글자 가독성용). 원본 픽셀을 바꾸지 않고 글자 '뒤'에만 깔림."""
    from PIL import ImageFilter
    W, H = size
    x0, y0, x1, y1 = sc["box"]
    m = Image.new("L", (W, H), 0)
    ImageDraw.Draw(m).rounded_rectangle([x0, y0, x1, y1], radius=S("scrim.corner_radius_px"), fill=int(255 * sc["alpha"]))
    m = m.filter(ImageFilter.GaussianBlur(sc["feather"] / 2))
    out = Image.new("RGBA", (W, H), (255, 255, 255, 0))
    out.putalpha(m)
    return out


def render(bg: Image.Image, layers_img: dict, layout) -> Image.Image:
    out = bg.convert("RGBA")
    if layout.get("scrim"):
        out = Image.alpha_composite(out, _scrim(out.size, layout["scrim"]))
    for L in layout["layers"]:
        src = layers_img[L["id"]]
        w, h = _scaled_size(src.width, src.height, L["scale"])
        im = src.resize((w, h), Image.LANCZOS) if (w, h) != src.size else src
        tmp = Image.new("RGBA", out.size, (0, 0, 0, 0))
        tmp.paste(im, (L["x"], L["y"]))
        out = Image.alpha_composite(out, tmp)
    return out.convert("RGB")
