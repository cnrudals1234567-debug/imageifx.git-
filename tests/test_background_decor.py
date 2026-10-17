"""flat 배경 재생성과 장식 앵커 배치."""
import numpy as np

from conftest import make_layer
from imageifx import background, layout


# ---------- background ----------

def _gradient(w=200, h=120):
    v, u = np.mgrid[0:h, 0:w]
    r = (250 + 4 * u / w).astype(np.uint8)
    g = (80 + 20 * v / h).astype(np.uint8)
    b = np.full((h, w), 5, np.uint8)
    return np.dstack([r, g, b])


def test_flat_fit_reproduces_a_gradient():
    arr = _gradient()
    model = background.fit_flat(arr, np.ones(arr.shape[:2], bool))
    out = np.asarray(background.render_flat(model, 200, 120)).astype(int)
    assert np.abs(out - arr.astype(int)).max() <= 2
    assert model["inlier"] > 0.95


def test_flat_fit_ignores_large_light_decor():
    """큰 장식(원호 등)이 섞여 있어도 지배적인 배경색을 맞춰야 한다(첫 맞춤이 장식 쪽으로 끌려가던 버그)."""
    arr = _gradient()
    arr[40:120, 0:90] = (230, 235, 240)             # 배경 면적의 약 30%를 덮는 밝은 장식
    model = background.fit_flat(arr, np.ones(arr.shape[:2], bool))
    out = np.asarray(background.render_flat(model, 200, 120)).astype(int)
    assert np.abs(out[:30, 100:] - _gradient().astype(int)[:30, 100:]).max() <= 3
    assert 0.5 < model["inlier"] < 0.9


def test_flat_fit_rejects_photo_like_noise():
    rng = np.random.default_rng(0)
    arr = rng.integers(0, 256, (120, 200, 3), dtype=np.uint8)
    model = background.fit_flat(arr, np.ones(arr.shape[:2], bool))
    assert model["inlier"] < 0.5, "사진·질감은 flat으로 쓰면 안 됨(inlier가 낮아 crop으로 되돌아감)"


# ---------- layout.place_decor ----------

PRESET = {"size": [1250, 560]}


def _lay(layers=()):
    return {"bg": {"scale": 1.0, "x": 0, "y": 0}, "layers": list(layers), "floor": {"y": 560, "src_y": 1200}}


def _decor(i, bbox, **kw):
    return {"id": i, "type": "decor", "importance": "decor", "bbox": bbox, **kw}


def test_decor_corner_anchor_keeps_distance_ratio():
    out = layout.place_decor(_lay(), [_decor("a", [60, 60, 160, 160], anchor="corner_tl")], PRESET, (1200, 1200))
    L = out["layers"][0]
    k = 560 / 1200
    assert (L["x"], L["y"]) == (round(60 * k), round(60 * k))
    assert L["placement"] == "anchored" and L["z"] < 0


def test_decor_bottom_anchor_sits_on_floor():
    out = layout.place_decor(_lay(), [_decor("arc", [0, 600, 400, 1200], anchor="corner_bl")], PRESET, (1200, 1200))
    L = out["layers"][0]
    assert L["y"] + round(600 * L["scale"]) == 560, "바닥 앵커는 바닥(띠 윗면)에 닿아야 함"


def test_decor_blocked_by_text_is_shrunk_then_dropped_with_reason():
    text = make_layer("t", 0, 0, 1250, 560)   # 캔버스 전체를 덮는 글자
    els = [_decor("opt", [10, 10, 110, 110], anchor="corner_tl", priority="optional", overlap="subject")]
    out = layout.place_decor(_lay([text]), els, PRESET, (1200, 1200))
    assert all(l["id"] != "opt" for l in out["layers"])
    assert out["dropped"][0]["id"] == "opt" and out["dropped"][0]["reason"], "제외 사유가 보고돼야 함(조용한 손실 금지)"


def test_decor_keep_priority_is_never_dropped():
    text = make_layer("t", 0, 0, 1250, 560)
    els = [_decor("must", [10, 10, 110, 110], anchor="corner_tl", priority="keep", overlap="subject")]
    out = layout.place_decor(_lay([text]), els, PRESET, (1200, 1200))
    assert any(l["id"] == "must" for l in out["layers"])


def test_decor_overlap_any_may_sit_behind_text():
    text = make_layer("t", 0, 0, 1250, 560)
    els = [_decor("wm", [10, 10, 110, 110], anchor="corner_tl", overlap="any", z="back")]
    out = layout.place_decor(_lay([text]), els, PRESET, (1200, 1200))
    L = next(l for l in out["layers"] if l["id"] == "wm")
    assert L["placement"] == "anchored"


def test_footer_text_does_not_block_decor_on_the_band():
    footer = make_layer("f", 0, 520, 1250, 30, group="footer")
    els = [_decor("arc", [0, 800, 300, 1200], anchor="corner_bl", overlap="subject")]
    out = layout.place_decor(_lay([footer]), els, PRESET, (1200, 1200))
    assert any(l["id"] == "arc" for l in out["layers"]), "띠 안 문구는 띠 위 장식과 다른 층"
