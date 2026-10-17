"""장식 후보 자동 탐지 (flat 배경 소재용).

배경 색장과 다른 연결 성분 중 이미 분석 json에 등록된 요소(글자·인물·띠) 밖에 있는 것을 장식 후보로 제안한다.
analyzer는 후보에 anchor/priority/overlap만 붙이면 되므로 bbox를 눈대중으로 잡지 않아도 된다.
"""
import numpy as np
import cv2

from . import background as bgm
from . import spec as _spec

S = _spec.get


def _nearest_corner(b, w, h):
    cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    return "corner_" + ("t" if cy < h / 2 else "b") + ("l" if cx < w / 2 else "r")


def candidates(arr, elements, min_area=1500, merge_px=15, threshold=None):
    h, w = arr.shape[:2]
    known = np.zeros((h, w), np.uint8)
    for e in elements:
        if e.get("bbox") and e["importance"] in ("critical", "band", "drop"):
            x0, y0, x1, y1 = e["bbox"]
            known[max(0, y0 - 4):y1 + 4, max(0, x0 - 4):x1 + 4] = 1
    occ = known.copy()
    for e in elements:
        if e.get("bbox"):
            x0, y0, x1, y1 = e["bbox"]
            occ[y0:y1, x0:x1] = 1
    model = bgm.fit_flat(arr, occ == 0, deg=S("background.flat_deg"))
    if model is None:
        return None, []
    flat = bgm.flat_at_source(model, w, h)
    thr = S("background.decor_threshold") if threshold is None else threshold
    m = ((np.abs(arr.astype(np.int32) - flat).max(axis=2) > thr) & (known == 0)).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * merge_px + 1,) * 2))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    out = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if area < min_area:
            continue
        b = [int(x), int(y), int(x + bw), int(y + bh)]
        out.append({"bbox": b, "area": int(area), "anchor": _nearest_corner(b, w, h)})
    out.sort(key=lambda c: -c["area"])
    return model, out
