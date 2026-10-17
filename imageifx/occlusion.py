"""글자가 인물(머리카락 등) 뒤에 가려진 곳을 찾고, 사용자가 '복원'을 고른 경우에만 가려진 획을 이어 그린다.
분해는 인물 아래 글자 픽셀을 지우므로(decompose), 글자와 인물을 떼어 놓으면 가려졌던 부분이 빈 채 드러난다.
- detect: 글자 bbox와 인물 실루엣이 맞닿아 글자가 끊긴 영역(R)을 구한다. 토큰 0.
- restore: 규칙 1의 사용자 승인 예외. R 안에서만 글자 모양(이진 마스크)을 LaMa로 이어 그린다. R 밖 글자 픽셀은 바이트 동일."""
import cv2
import numpy as np

from . import fill
from . import spec as _spec

S = _spec.get


def region(subj_full, bbox, text_alpha):
    """글자가 인물 실루엣에 가려졌을 영역 R(원본 전체 좌표 bool). 글자 bbox 안에서 실루엣을 조금 넓힌 곳,
    그리고 그 옆에 글자 픽셀이 실제로 닿아 있을 때만(=글자가 거기서 끊겼을 때만) 돌려준다."""
    x0, y0, x1, y1 = bbox
    k = S("occlusion.dilate_px")
    R = cv2.dilate((subj_full > 0).astype(np.uint8), np.ones((k, k), np.uint8)) > 0
    box = np.zeros(R.shape, bool)
    box[y0:y1, x0:x1] = True
    R &= box
    if not R.any():
        return R
    g = np.zeros(R.shape, bool)
    g[y0:y1, x0:x1] = text_alpha > 128
    n = S("occlusion.touch_px")
    touch = cv2.dilate(R.astype(np.uint8), np.ones((2 * n + 1, 2 * n + 1), np.uint8)) > 0
    if (touch & g).sum() < S("occlusion.min_touch_px"):
        return np.zeros(R.shape, bool)
    return R


def baseline(g, R):
    """R 바로 위 줄의 글자 바닥 y. R 가로 범위 ± margin 열에서 글자가 있는 열의 맨 아래 행을 모아 윗쪽 30%의 중앙값."""
    ys, xs = np.where(R)
    m = S("occlusion.baseline_margin_px")
    c0, c1 = max(0, xs.min() - m), min(g.shape[1], xs.max() + m)
    band_top = max(0, ys.min() - S("occlusion.line_up_px"))
    bottoms = []
    for x in range(c0, c1):
        col = np.where(g[band_top:ys.max() + 1, x])[0]
        if len(col):
            bottoms.append(band_top + col.max())
    if not bottoms:
        return int(ys.max())
    bottoms.sort()
    return int(np.median(bottoms[int(len(bottoms) * 0.7):]))


def restore(layer, bbox, R):
    """layer: bbox 크기 RGBA(글자 레이어, 인물 아래는 이미 투명). R: 원본 전체 좌표 bool.
    반환: (새 RGBA 레이어, 복원 픽셀 수). R 밖은 그대로."""
    x0, y0, x1, y1 = bbox
    arr = np.asarray(layer).copy()
    a = arr[..., 3]
    full = np.zeros(R.shape, bool)
    full[y0:y1, x0:x1] = a > 128
    base = baseline(full, R)
    R = R.copy()
    R[base + 1:] = False
    if not R.any():
        return layer, 0
    ys, xs = np.where(R)
    pad = S("occlusion.context_px")
    cx0, cy0, cx1, cy1 = max(0, xs.min() - pad), max(0, ys.min() - 2 * pad), min(R.shape[1], xs.max() + pad), min(R.shape[0], base + pad)
    img = np.where(full, 255, 0).astype(np.uint8)
    img[R] = 0
    from PIL import Image
    sub = Image.fromarray(np.stack([img] * 3, -1)).crop((cx0, cy0, cx1, cy1))
    out = np.asarray(fill.inpaint(sub, R[cy0:cy1, cx0:cx1]))[..., 0]
    rest = np.zeros(R.shape, bool)
    rest[cy0:cy1, cx0:cx1] = out > 128
    new = rest & R
    # 잡티(머리카락 결을 따라 생긴 가는 획)를 열기로 제거
    k = S("occlusion.open_px")
    solid = (full | new).astype(np.uint8)
    solid = cv2.morphologyEx(solid, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    new &= solid > 0
    # R 주변은 머리카락 경계의 반투명·오염 픽셀이 섞여 있으므로, 이진 글자 모양으로 다시 확정한다(색은 글자 색 중앙값)
    zone = cv2.dilate(R.astype(np.uint8), np.ones((S("occlusion.zone_px"),) * 2, np.uint8)) > 0
    k1, k2 = S("occlusion.close_px"), S("occlusion.clean_px")
    solid = cv2.morphologyEx((full | new).astype(np.uint8), cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k1, k1)))
    solid = cv2.morphologyEx(solid, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k2, k2)))
    sa = cv2.GaussianBlur(solid.astype(np.float32) * 255, (0, 0), S("occlusion.aa_sigma"))
    solid_pix = arr[..., :3][a > 250]
    col = np.median(solid_pix, axis=0).astype(np.uint8) if len(solid_pix) else np.array([255, 255, 255], np.uint8)
    zb = zone[y0:y1, x0:x1]
    arr[..., 3] = np.where(zb, sa[y0:y1, x0:x1], a).astype(np.uint8)
    arr[zb, :3] = col
    from PIL import Image as _I
    return _I.fromarray(arr, "RGBA"), int(new.sum())
