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

def _txt(i, x0, x1, y=None):
    """y를 주지 않으면 id 순서대로 서로 다른 줄(높이)에 둔다."""
    y = (ord(i[0]) - ord("a")) * 40 if y is None else y
    return {"id": i, "bbox": [x0, y, x1, y + 10]}


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


def test_align_dx_keeps_side_by_side_items_together():
    """같은 높이에 나란히 있는 요소(알약 2개)를 따로 가운데로 당기면 서로 겹친다(실제로 났던 버그)."""
    texts = [_txt("a", 100, 1100, 0), _txt("b", 200, 590, 100), _txt("c", 600, 1000, 100),
             _txt("d", 300, 900, 200)]
    dx = layout._align_dx(texts)
    assert dx.get("b", 0) == dx.get("c", 0), "한 줄의 요소는 같은 양만큼 움직여야 함"


# ---------- auto_free: 띠·bleed·상단 여백 ----------

def _el(i, typ, imp, bbox, **kw):
    return {"id": i, "type": typ, "importance": imp, "bbox": bbox, **kw}


def _square_preset():
    return {"size": [1080, 1080], "mode": "free", "layout": "free", "safe_margin": {"x": 48, "y": 48}, "min_gap": 16,
            "min_text_height": 22}


def test_auto_free_places_band_and_footer_above_bottom_margin():
    els = [_el("title", "text", "critical", [100, 0, 1100, 300]),
           _el("woman", "subject", "critical", [300, 500, 900, 1074]),
           _el("foot", "text", "critical", [90, 1085, 1120, 1190], group="footer"),
           _el("band", "decor", "band", [0, 1075, 1200, 1200])]
    lay = layout.auto_free(els, (1200, 1200), _square_preset())
    assert lay["band"]["h"] > 0 and lay["floor"]["y"] == 1080 - lay["band"]["h"]
    foot = next(L for L in lay["layers"] if L["id"] == "foot")
    h = round((foot["src_bbox"][3] - foot["src_bbox"][1]) * foot["scale"])
    assert foot["group"] == "footer" and foot["y"] >= lay["floor"]["y"] and foot["y"] + h <= 1080 - 48   # 띠 안, 하단 여백 지킴
    woman = next(L for L in lay["layers"] if L["id"] == "woman")
    assert woman.get("on_band") and woman["y"] + round(574 * woman["scale"]) <= lay["floor"]["y"] + 1
    title = next(L for L in lay["layers"] if L["id"] == "title")
    assert title["y"] >= 48   # 상단 여백(예전에는 0이라 제목이 맨 위에 붙었다)


def test_auto_free_keeps_text_inside_bottom_margin_when_subject_bleeds():
    els = [_el("body", "text", "critical", [600, 900, 1150, 1150]),
           _el("person", "subject", "critical", [30, 650, 600, 1200])]   # 원본 바닥(1200)에 닿음 -> bleed
    lay = layout.auto_free(els, (1200, 1200), _square_preset())
    person = next(L for L in lay["layers"] if L["id"] == "person")
    body = next(L for L in lay["layers"] if L["id"] == "body")
    assert "bleed" in person and person["y"] + round(550 * person["scale"]) == 1080
    assert body["y"] + round(250 * body["scale"]) <= 1080 - 48    # 글자는 하단 여백을 넘지 않는다


# ---------- 원본 통째 유지(layout:whole) ----------

def test_whole_keeps_pixels_and_fits_text_in_safe_zone():
    import json, os
    from PIL import Image
    preset = json.load(open(os.path.join(os.path.dirname(__file__), "..", "presets", "presets.json"), encoding="utf-8"))["gfa_1250x560"]
    els = [{"id": "t", "type": "text", "importance": "critical", "bbox": [200, 50, 1000, 700]}]
    src = Image.new("RGB", (1254, 1254), (10, 60, 200))
    clean, layers = decompose.run_whole(src, els)
    assert list(clean.getdata()) == list(src.getdata())              # 글자를 지우거나 바꾸지 않는다
    lay = layout.auto_whole(els, src.size, preset)
    L = lay["layers"][0]
    assert L["ghost"] and abs(lay["bg"]["scale"] - L["scale"]) < 1e-3            # 글자 레이어는 검사용, 배경과 같은 배율
    sz = preset["safe_zone"]
    W, H = preset["size"]
    x1, y1 = L["x"] + 800 * L["scale"], L["y"] + 650 * L["scale"]
    assert L["x"] >= sz["left"] - 1 and x1 <= W - sz["right"] + 1 and L["y"] >= sz["top"] - 1 and y1 <= H - sz["bottom"] + 1
