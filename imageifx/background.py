"""단색·그라데이션 배경을 '잘라 쓰기' 대신 색 모델로 다시 만든다.

글자·인물을 지운 자리를 LaMa로 메운 픽셀이 화면에 나오지 않으므로 얼룩·잔상이 원리적으로 생기지 않고,
어떤 캔버스 비율로든 이음새 없이 늘어난다. 사진·질감 배경은 잔차가 커서 자동으로 쓰지 않는다.
"""
import numpy as np


def _design(u, v, deg):
    cols = [np.ones_like(u)]
    if deg >= 1:
        cols += [u, v]
    if deg >= 2:
        cols += [u * u, u * v, v * v]
    return np.stack(cols, axis=-1)


def fit_flat(arr, use_mask, deg=1, region=None, step=3, iters=4, inlier_px=12):
    """use_mask가 True인 픽셀로 채널별 다항식 색장을 맞춘다.
    강건 맞춤: 맞춘 뒤 잔차가 inlier_px보다 큰 픽셀(장식·사진 등)을 빼고 다시 맞추기를 반복해 지배적인 배경색에 수렴시킨다.
    region: 정규화 좌표의 기준 영역 [x0,y0,x1,y1] (기본: 이미지 전체).
    반환: resid(배경으로 남은 픽셀의 잔차 표준편차), inlier(배경으로 남은 비율). 사진 배경이면 inlier가 낮다."""
    h, w = arr.shape[:2]
    x0, y0, x1, y1 = region or [0, 0, w, h]
    ys, xs = np.nonzero(use_mask[::step, ::step])
    ys, xs = ys * step, xs * step
    if len(xs) < 50:
        return None
    u = (xs - x0) / max(1, x1 - x0)
    v = (ys - y0) / max(1, y1 - y0)
    X = _design(u.astype(np.float64), v.astype(np.float64), deg)
    Y = arr[ys, xs].astype(np.float64)
    # 시작점: 전체 중앙값(지배적인 색)에서 가까운 픽셀만. 큰 장식이 섞여도 첫 맞춤이 그쪽으로 끌려가지 않게 한다.
    keep = np.abs(Y - np.median(Y, axis=0)).max(axis=1) < inlier_px * 2
    if keep.sum() < 50:
        keep = np.ones(len(Y), bool)
    for _ in range(iters):
        coef, *_ = np.linalg.lstsq(X[keep], Y[keep], rcond=None)
        r = np.abs(Y - X @ coef).max(axis=1)
        new = r < inlier_px
        if new.sum() < 50 or (new == keep).all():
            break
        keep = new
    # 보고 값은 '최종 모델 기준'으로 다시 계산한다(맞춤이 중간에 포기해도 기본값이 그대로 보고되지 않게).
    err = Y - X @ coef
    inl = np.abs(err).max(axis=1) < inlier_px
    resid = float(err[inl].std()) if inl.sum() >= 50 else float(err.std())
    return {"deg": deg, "coef": coef.round(6).tolist(), "resid": round(resid, 3), "inlier": round(float(inl.mean()), 3)}


def render_flat(model, W, H):
    """모델을 W x H 영역에 그린다(정규화 좌표 0~1을 캔버스 전체에 대응)."""
    from PIL import Image
    v, u = np.mgrid[0:H, 0:W].astype(np.float64)
    u = u / max(1, W - 1) if W > 1 else u
    v = v / max(1, H - 1) if H > 1 else v
    out = _design(u, v, model["deg"]) @ np.asarray(model["coef"], np.float64)
    return Image.fromarray(np.clip(np.round(out), 0, 255).astype(np.uint8), "RGB")


def flat_at_source(model, w, h):
    """원본 크기에서의 배경 색장(장식 알파 계산용)."""
    return np.asarray(render_flat(model, w, h)).astype(np.int32)
