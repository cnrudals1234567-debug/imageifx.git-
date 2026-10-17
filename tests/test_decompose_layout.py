"""영역 자동 보정, 정렬 정규화, 장식 재배치 (이번 개발에서 버그가 났던 로직)."""
import numpy as np

from conftest import make_layer
from imageifx import decompose, layout


# ---------- decompose._fit_bbox ----------

def _board(w=200, h=100):
    return np.full((h, w, 3), 30, np.uint8)   # 어두운 칠판


def test_fit_bbox_grows_when_glyph_is_clipped():
    arr = _board()
    arr[20:60, 60:140] = 255                      # 글자(밝은 덩어리): 위쪽이 y=20부터 시작, 좌우는 bbox 안쪽
    nb = decompose._fit_bbox(arr, [50, 30, 150, 60], others=[])
    assert nb[1] <= 20, "bbox 위쪽이 글자 시작(y=20)까지 넓어져야 함"
    assert nb[1] >= 8, "무한정 넓어지면 안 됨"
    assert nb[0] == 50 and nb[2] == 150           # 닿지 않은 방향은 그대로


def test_fit_bbox_unchanged_when_margin_exists():
    arr = _board()
    arr[20:60, 50:150] = 255
    assert decompose._fit_bbox(arr, [40, 10, 160, 70], others=[]) == [40, 10, 160, 70]


def test_fit_bbox_stops_at_neighbor():
    arr = _board()
    arr[20:60, 50:150] = 255
    nb = decompose._fit_bbox(arr, [50, 30, 150, 60], others=[[0, 0, 200, 18]])
    assert nb[1] >= 19, "이웃 요소(y<=18) 영역을 침범하면 안 됨"


def test_fit_bbox_ignores_preexisting_overlap():
    """원래 이웃과 겹쳐 있던 부분 때문에 다른 방향 넓힘까지 막히면 안 된다(이번에 실제로 났던 버그)."""
    arr = _board()
    arr[20:60, 50:190] = 255                      # 오른쪽 끝(x=190)까지 글자가 이어짐
    others = [[0, 55, 100, 100]]                  # bbox 왼쪽 아래와 이미 겹치지만, 오른쪽 띠(x 150~)와는 겹치지 않는 이웃
    nb = decompose._fit_bbox(arr, [50, 30, 150, 60], others=others)
    assert nb[2] > 150, "새로 넓어지는 띠만 이웃과 비교해야 함"


# ---------- layout._align_dx ----------

def _txt(i, x0, x1):
    return {"id": i, "bbox": [x0, 0, x1, 10]}


def test_align_dx_recenters_only_outliers_in_centered_design():
    texts = [_txt("a", 500, 700), _txt("b", 450, 750), _txt("c", 400, 800), _txt("d", 700, 900)]   # d 중심만 크게 어긋남
    dx = layout._align_dx(texts)
    assert set(dx) == {"d"}
    assert dx["d"] < 0                              # 왼쪽으로 당겨야 함


def test_align_dx_leaves_left_aligned_design_alone():
    texts = [_txt("a", 100, 300), _txt("b", 100, 500), _txt("c", 100, 400), _txt("d", 100, 250)]
    assert layout._align_dx(texts) == {}


def test_align_dx_needs_three_lines():
    assert layout._align_dx([_txt("a", 0, 100), _txt("b", 300, 500)]) == {}


# ---------- layout.place_movable ----------

def _preset():
    return {"size": [1250, 560]}


def _base_layout(layers=()):
    return {"bg": {"scale": 1.0, "x": 0, "y": 0}, "layers": list(layers)}


def test_movable_stays_in_place_when_free():
    els = [{"id": "star", "importance": "movable", "bbox": [100, 100, 160, 160]}]
    out = layout.place_movable(_base_layout(), els, _preset())
    L = out["layers"][0]
    assert (L["x"], L["y"], L["placement"]) == (100, 100, "in_place")


def test_movable_relocates_when_blocked_and_never_overlaps():
    block = make_layer("headline", 90, 90, 300, 200)
    els = [{"id": "star", "importance": "movable", "bbox": [100, 100, 160, 160]}]
    out = layout.place_movable(_base_layout([block]), els, _preset())
    L = next(l for l in out["layers"] if l["id"] == "star")
    assert L["placement"] == "relocated"
    sx1, sy1 = L["x"] + 60 * L["scale"], L["y"] + 60 * L["scale"]
    assert not (L["x"] < 390 and sx1 > 90 and L["y"] < 290 and sy1 > 90), "필수 요소와 겹치면 안 됨"


def test_movable_stay_y_moves_horizontally_only():
    block = make_layer("cta", 190, 400, 200, 100)
    els = [{"id": "chalk", "importance": "movable", "stay_y": True, "bbox": [200, 450, 260, 480]}]
    out = layout.place_movable(_base_layout([block]), els, _preset())
    L = next(l for l in out["layers"] if l["id"] == "chalk")
    assert L["y"] == 450, "받침대 위 소품은 높이를 유지해야 함(허공에 뜨면 안 됨)"
    assert L["x"] != 200


def test_movable_dropped_when_no_room():
    block = make_layer("wall", 0, 0, 1250, 560)    # 캔버스 전체를 가림
    els = [{"id": "star", "importance": "movable", "bbox": [100, 100, 160, 160]}]
    out = layout.place_movable(_base_layout([block]), els, _preset())
    assert all(l["id"] != "star" for l in out["layers"]), "자리가 없으면 허공에 두지 말고 누락으로 보고(인벤토리가 LOST 처리)"
