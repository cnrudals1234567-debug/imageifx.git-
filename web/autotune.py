"""무료 자동 보정(토큰 0): 글자 블록을 이동·균등 축소해 규정 FAIL이 가장 적은 배치를 찾는다.

글자 내용·폰트는 건드리지 않고(규칙 1) 위치와 균등 배율만 바꾼다. 평가는 imageifx.validate의 배치 검사와 같은
실루엣 간격 규칙을 쓰되, 피사체 마스크를 한 번만 계산해 후보 수천 개를 빠르게 비교한다."""
import copy

import cv2
import numpy as np

from imageifx import spec, validate
from . import ops

STEP = 8
RANGE = 160
FACTORS = (1.0, 0.96, 0.92, 0.88, 0.84, 0.80)


def _masks(layout, preset, layers):
    """overlap_ok 피사체의 gap 팽창 실루엣 마스크(피사체는 움직이지 않으므로 한 번만)."""
    W, H = preset["size"]
    gap = preset["min_gap"]
    out = []
    for S in [L for L in layout["layers"] if L.get("overlap_ok") and L["id"] in layers]:
        im = layers[S["id"]]
        w, h = max(1, round(im.width * S["scale"])), max(1, round(im.height * S["scale"]))
        a = np.asarray(im.resize((w, h)))[..., 3] > 128
        canvas = np.zeros((H, W), np.uint8)
        cx0, cy0, cx1, cy1 = max(0, S["x"]), max(0, S["y"]), min(W, S["x"] + w), min(H, S["y"] + h)
        if cx1 > cx0 and cy1 > cy0:
            canvas[cy0:cy1, cx0:cx1] = a[cy0 - S["y"]:cy1 - S["y"], cx0 - S["x"]:cx1 - S["x"]]
        out.append((S, cv2.dilate(canvas, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * gap + 1, 2 * gap + 1)))))
    return out


def count_fails(layout, preset, masks):
    """배치 규정 FAIL 수(안전영역·간격·글자 높이 + 실루엣 간격)."""
    W, H = preset["size"]
    n = sum(1 for r in validate.check(layout, preset) if not r["ok"])
    # 글자 블록을 옮기다 재배치된 장식(movable)과 부딪히면 눈으로만 보이는 결함이 되므로 FAIL로 센다
    gap = spec.get("movable.gap_px")
    decor = [ops.box(D) for D in layout["layers"] if D["type"] == "decor"]
    for T in layout["layers"]:
        if T["type"] == "text":
            b = ops.box(T)
            n += sum(1 for d in decor if not (b[2] + gap <= d[0] or d[2] + gap <= b[0] or b[3] + gap <= d[1] or d[3] + gap <= b[1]))
    for S, grown in masks:
        for T in layout["layers"]:
            if T["type"] != "text" or (S.get("on_band") and T.get("group") == "footer"):
                continue
            b = ops.box(T)
            if grown[max(0, b[1]):min(H, b[3]), max(0, b[0]):min(W, b[2])].any():
                n += 1
    return n


def _candidates():
    cs = []
    for f in FACTORS:
        for dy in range(-RANGE, RANGE + 1, STEP):
            for dx in range(-RANGE, RANGE + 1, STEP):
                cs.append(((abs(dx) + abs(dy)) / STEP + (1 - f) * 400, dx, dy, f))
    cs.sort()
    return cs


def solve(layout, preset, layers):
    """(새 layout, 설명, 이전 FAIL 수, 이후 FAIL 수). 개선이 없으면 새 layout은 None."""
    masks = _masks(layout, preset, layers)
    base = count_fails(layout, preset, masks)
    if base == 0 or not ops.group_layers(layout, "text"):
        return None, "배치 규정 FAIL이 없습니다", base, base
    best = (base, None)
    for cost, dx, dy, f in _candidates():
        if cost == 0:
            continue
        lay = copy.deepcopy(layout)
        ops.transform_text_block(lay, dx, dy, f)
        n = count_fails(lay, preset, masks)
        if n < best[0]:
            best = (n, (dx, dy, f, lay))
            if n == 0:
                break
    if best[1] is None:
        return None, "글자 블록 이동·축소로는 FAIL이 줄지 않았습니다", base, base
    dx, dy, f, lay = best[1]
    lay.setdefault("manual_ops", []).append({"op": "autotune", "dx": dx, "dy": dy, "factor": f})
    return lay, f"글자 블록 이동({dx:+d},{dy:+d})px, 배율 ×{f}", base, best[0]
