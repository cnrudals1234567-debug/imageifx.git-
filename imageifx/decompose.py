"""원본 -> 클린 배경 + 레이어(텍스트/피사체) 분해. 레이어는 원본 픽셀 그대로."""
import numpy as np
import cv2
from PIL import Image
from . import fill
from . import spec as _spec

S = _spec.get


def _bbox_mask(shape, boxes, pad):
    m = np.zeros(shape[:2], np.uint8)
    h, w = shape[:2]
    for x0, y0, x1, y1 in boxes:
        m[max(0, y0 - pad):min(h, y1 + pad), max(0, x0 - pad):min(w, x1 + pad)] = 1
    return m


def _subject_alpha(crop: Image.Image) -> np.ndarray:
    from rembg import remove
    out = remove(crop.convert("RGB"))
    return np.asarray(out.convert("RGBA"))[..., 3]


def _fit_bbox(arr, bbox, others, T=None, step=None, max_grow=None):
    """글자 bbox 경계 안쪽 띠에 글자(주변 배경과 다른 픽셀)가 닿아 있으면 그 방향으로 넓힌다.
    사람이 눈대중으로 잡은 bbox가 글자·낙서를 가로로 자르는 문제를 막는다. 이웃 요소 bbox와는 겹치지 않게 멈춘다."""
    T = S("decompose.fit.threshold") if T is None else T
    step = S("decompose.fit.step_px") if step is None else step
    max_grow = S("decompose.fit.max_grow_px") if max_grow is None else max_grow
    fine = S("decompose.fit.fine_steps_px")
    band = S("decompose.fit.edge_band_px")
    min_hits = S("decompose.fit.min_hits")
    ri, ro = S("decompose.fit.ring_inner_px"), S("decompose.fit.ring_outer_px")
    h, w = arr.shape[:2]
    x0, y0, x1, y1 = bbox

    def ring_color(x0, y0, x1, y1):
        X0, Y0, X1, Y1 = max(0, x0 - ro), max(0, y0 - ro), min(w, x1 + ro), min(h, y1 + ro)
        m = np.ones((Y1 - Y0, X1 - X0), bool)
        m[max(0, y0 - ri - Y0):y1 + ri - Y0, max(0, x0 - ri - X0):x1 + ri - X0] = False
        px = arr[Y0:Y1, X0:X1][m]
        return np.median(px, axis=0) if len(px) else np.array([0, 0, 0])

    def free(b):
        return not any(not (b[2] <= o[0] - 1 or o[2] + 1 <= b[0] or b[3] <= o[1] - 1 or o[3] + 1 <= b[1]) for o in others)

    grown = 0
    for _ in range(max_grow // step):
        bg = ring_color(x0, y0, x1, y1)
        fg = np.abs(arr.astype(np.int32) - bg.astype(np.int32)).max(axis=2) > T
        changed = False
        for side in ("top", "bottom", "left", "right"):
            touch = {
                "top": y0 > 0 and fg[y0:y0 + band, x0:x1].sum() >= min_hits,
                "bottom": y1 < h and fg[y1 - band:y1, x0:x1].sum() >= min_hits,
                "left": x0 > 0 and fg[y0:y1, x0:x0 + band].sum() >= min_hits,
                "right": x1 < w and fg[y0:y1, x1 - band:x1].sum() >= min_hits,
            }[side]
            if not touch:
                continue
            for stp in (step, *fine):   # 이웃에 막히면 더 작은 폭으로 시도 (1px만 겹쳐도 통째로 막히는 것 방지)
                if side == "top":
                    nb = [x0, max(0, y0 - stp), x1, y1]; st = [x0, nb[1], x1, y0]
                elif side == "bottom":
                    nb = [x0, y0, x1, min(h, y1 + stp)]; st = [x0, y1, x1, nb[3]]
                elif side == "left":
                    nb = [max(0, x0 - stp), y0, x1, y1]; st = [nb[0], y0, x0, y1]
                else:
                    nb = [x0, y0, min(w, x1 + stp), y1]; st = [x1, y0, nb[2], y1]
                if nb != [x0, y0, x1, y1] and free(st):   # 새로 넓어지는 띠만 이웃과 겹치는지 본다
                    x0, y0, x1, y1 = nb
                    changed = True
                    break
        if not changed:
            break
        grown += 1
    return [x0, y0, x1, y1]


def _edge_bg_color(arr, bbox, w=3):
    """영역 가장자리 안쪽 w px 띠의 중앙값 = 배경색. 굵은 글씨는 영역의 절반 이상이 글자라 영역 전체 중앙값은 글자색이 되기 때문에
    (자동 보정으로 글자가 가장자리에 닿지 않게 맞춘 뒤의) 가장자리 띠를 쓴다."""
    x0, y0, x1, y1 = bbox
    r = arr[y0:y1, x0:x1]
    strips = [r[:w].reshape(-1, 3), r[-w:].reshape(-1, 3), r[:, :w].reshape(-1, 3), r[:, -w:].reshape(-1, 3)]
    return np.median(np.concatenate(strips), axis=0).astype(np.int32)


def _glyph_mask(arr, bbox, pad):
    """글자 모양대로 지울 마스크. bbox 안 배경색(중앙값)과 다른 픽셀만 팽창해서 반환한다.
    단색 배경에서 사각형 통째로 LaMa에 맡길 때 생기는 탁한 얼룩과, 하단 띠 같은 배경 요소가 같이 지워지는 문제를 피한다."""
    h, w = arr.shape[:2]
    x0, y0, x1, y1 = bbox
    X0, Y0, X1, Y1 = max(0, x0 - pad), max(0, y0 - pad), min(w, x1 + pad), min(h, y1 + pad)
    bgc = _edge_bg_color(arr, bbox)
    fg = (np.abs(arr[Y0:Y1, X0:X1].astype(np.int32) - bgc).max(axis=2) > S("decompose.glyph.threshold")).astype(np.uint8)
    k = S("decompose.glyph.dilate_px")
    fg = cv2.dilate(fg, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * k + 1, 2 * k + 1)))
    m = np.zeros((h, w), np.uint8)
    m[Y0:Y1, X0:X1] = fg
    return m


def run(src: Image.Image, elements: list, pad: int = None, text_mask: str = "bbox"):
    """elements: [{id,type,importance,bbox:[x0,y0,x1,y1]}] (원본 좌표).
    반환: clean_bg(RGB), layers{id: RGBA Image(bbox 크기)}"""
    pad = S("decompose.mask_pad_px") if pad is None else pad
    sat = S("decompose.subject_alpha_threshold")
    ta = S("decompose.text_alpha")
    src = src.convert("RGB")
    arr = np.asarray(src)

    # 글자 bbox 자동 보정: elements의 bbox를 제자리에서 갱신(이후 레이아웃/elements.json에 반영됨)
    for e in elements:
        if e["type"] == "text" and e["importance"] == "critical":
            others = [o["bbox"] for o in elements if o is not e and o.get("bbox") and o["importance"] != "band"]
            nb = _fit_bbox(arr, e["bbox"], others)
            if nb != e["bbox"]:
                print(f"[bbox 보정] {e['id']}: {e['bbox']} -> {nb}")
                e["bbox_original"] = e["bbox"]
                e["bbox"] = nb
    crit = [e for e in elements if e["importance"] in ("critical", "movable")]  # movable: 장식이지만 재배치할 요소

    # 1) 피사체 알파 먼저 계산 (마스크 정밀화용)
    alphas = {}
    for e in crit:
        x0, y0, x1, y1 = e["bbox"]
        crop = src.crop((x0, y0, x1, y1))
        if e["type"] == "subject":
            alphas[e["id"]] = _subject_alpha(crop)

    # 2) 지울 영역 마스크: 텍스트=bbox+pad, 피사체=알파 팽창
    mask = np.zeros(arr.shape[:2], np.uint8)
    for e in crit:
        x0, y0, x1, y1 = e["bbox"]
        if e["type"] == "subject":
            full = np.zeros(arr.shape[:2], np.uint8)
            full[y0:y1, x0:x1] = (alphas[e["id"]] > sat)
            # bbox 바깥으로도 팽창해야 경계에 잔상(선)이 남지 않음
            mask |= cv2.dilate(full, np.ones((2 * pad + 1,) * 2, np.uint8))
        elif e["type"] == "text" and e.get("mask", text_mask) == "glyph":
            mask |= _glyph_mask(arr, e["bbox"], pad)
        else:
            mask |= _bbox_mask(arr.shape, [e["bbox"]], pad)
    # importance="drop": 배경에서 지우기만 하고 배치하지 않음 (옮겨진 글자와 겹치는 낙서 등)
    for e in elements:
        if e["importance"] == "drop":
            mask |= _bbox_mask(arr.shape, [e["bbox"]], pad)
    clean = fill.inpaint(src, mask)

    # 피사체 영역(원본 위치)은 글자 레이어에서 제외: 겹친 bbox에서 피사체 픽셀이 글자로 딸려오는 것 방지
    subj_full = np.zeros(arr.shape[:2], np.uint8)
    for e in crit:
        if e["type"] == "subject":
            x0, y0, x1, y1 = e["bbox"]
            subj_full[y0:y1, x0:x1] |= (alphas[e["id"]] > sat)
    carr = np.asarray(clean).astype(np.int16)

    # 3) 레이어 추출
    layers = {}
    for e in crit:
        x0, y0, x1, y1 = e["bbox"]
        rgb = arr[y0:y1, x0:x1]
        if e["type"] == "subject":
            alpha = alphas[e["id"]]
        elif e["type"] == "text" and e.get("mask", text_mask) == "glyph":
            # 글자 모양 모드: 영역 안 배경색과의 거리로 알파를 만든다(연한 워터마크는 거리가 작아 제외됨)
            gl = S("decompose.glyph")
            bgc = _edge_bg_color(arr, e["bbox"])
            dist = np.abs(arr[y0:y1, x0:x1].astype(np.int32) - bgc).max(axis=2)
            alpha = np.clip((dist - gl["alpha_floor"]) * 255 / float(gl["alpha_span"]), 0, 255).astype(np.uint8)
            alpha = cv2.dilate(alpha, np.ones((ta["dilate_px"], ta["dilate_px"]), np.uint8))
            alpha[subj_full[y0:y1, x0:x1] > 0] = 0
        else:  # 텍스트: 원본-클린 차이로 글자 픽셀만 알파화
            diff = np.abs(arr[y0:y1, x0:x1].astype(np.int32) - carr[y0:y1, x0:x1].astype(np.int32)).max(axis=2)
            alpha = np.clip((diff - ta["floor"]) * 255 / float(ta["span"]), 0, 255).astype(np.uint8)
            alpha = cv2.dilate(alpha, np.ones((ta["dilate_px"], ta["dilate_px"]), np.uint8))  # 외곽선/그림자 보존
            alpha[subj_full[y0:y1, x0:x1] > 0] = 0
        layers[e["id"]] = Image.fromarray(np.dstack([rgb, alpha]), "RGBA")
    return clean, layers
