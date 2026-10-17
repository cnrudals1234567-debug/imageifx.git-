"""규정·글자 무결성·요소 보존 검사."""
import numpy as np
from PIL import Image

from conftest import make_layer
from imageifx import validate


def _by_name(rows, prefix):
    return [r for r in rows if r["check"].startswith(prefix)]


def test_text_inside_safe_zone_passes_outside_fails(gfa):
    inside = make_layer("t1", 300, 100, 200, 50)                 # 안전영역 x 240~1010, y 50~525
    outside = make_layer("t2", 1100, 100, 100, 50)               # 우측 240px 구역
    rows = validate.check({"layers": [inside, outside]}, gfa, out_size=(1250, 560), file_kb=200)
    r = {x["check"]: x["ok"] for x in _by_name(rows, "안전영역 글자")}
    assert r["안전영역 글자 [t1]"] is True
    assert r["안전영역 글자 [t2]"] is False


def test_subject_outside_safe_zone_is_warning_not_failure(gfa):
    subj = make_layer("owl", 0, 100, 400, 460, typ="subject", bleed=["bottom"])
    rows = validate.check({"layers": [subj]}, gfa, out_size=(1250, 560), file_kb=200)
    assert all(r["ok"] for r in rows), "피사체는 안전영역 밖이어도 hard FAIL이 아님"
    assert any(r.get("warn") for r in _by_name(rows, "안전영역 안 비율"))


def test_file_size_limits(gfa):
    for kb, ok_hi, ok_lo in ((200, True, True), (250, False, True), (30, True, False)):
        rows = validate.check({"layers": []}, gfa, out_size=(1250, 560), file_kb=kb)
        assert _by_name(rows, "파일 용량 상한")[0]["ok"] is ok_hi
        assert _by_name(rows, "파일 용량 하한")[0]["ok"] is ok_lo


def test_wrong_pixel_size_fails(gfa):
    rows = validate.check({"layers": []}, gfa, out_size=(1200, 560), file_kb=200)
    assert _by_name(rows, "출력 픽셀 크기")[0]["ok"] is False


def test_bleed_must_touch_canvas_edge(gfa):
    short = make_layer("owl", 100, 100, 300, 455, typ="subject", bleed=["bottom"])    # 하단 555 < 560
    rows = validate.check({"layers": [short]}, gfa, out_size=(1250, 560), file_kb=200)
    assert _by_name(rows, "bleed 하단 접촉")[0]["ok"] is False


# ---------- 글자 잘림 ----------

def _layer_img(w, h, box):
    a = np.zeros((h, w, 4), np.uint8)
    x0, y0, x1, y1 = box
    a[y0:y1, x0:x1] = (255, 255, 255, 255)
    return Image.fromarray(a, "RGBA")


def test_clipped_text_is_detected(gfa):
    lay = {"layers": [make_layer("clipped", 300, 100, 200, 60), make_layer("clean", 300, 200, 200, 60)]}
    layers = {"clipped": _layer_img(200, 60, (20, 0, 150, 40)),      # 글자가 레이어 윗경계(y=0)에 걸림
              "clean": _layer_img(200, 60, (20, 10, 150, 50))}
    rows = {r["check"]: r["ok"] for r in validate.check_text_integrity(lay, gfa, layers) if r["check"].startswith("글자 잘림")}
    assert rows["글자 잘림 [clipped]"] is False
    assert rows["글자 잘림 [clean]"] is True


def test_misaligned_line_is_flagged_in_centered_design(gfa):
    lay = {"layers": [make_layer("a", 400, 100, 300, 40), make_layer("b", 380, 160, 340, 40),
                      make_layer("c", 360, 220, 380, 40), make_layer("d", 640, 280, 300, 40)]}   # d만 오른쪽으로 치우침
    layers = {k: _layer_img(L["src_bbox"][2], 40, (10, 10, L["src_bbox"][2] - 10, 30)) for k, L in zip("abcd", lay["layers"])}
    rows = validate.check_text_integrity(lay, gfa, layers)
    align = _by_name(rows, "글자 줄 정렬")[0]
    assert align["warn"] is True


# ---------- 요소 보존 ----------

def test_inventory_reports_lost_critical_and_dropped(gfa):
    els = [{"id": "headline", "importance": "critical", "bbox": [0, 0, 100, 50]},
           {"id": "bulb", "importance": "drop", "bbox": [0, 0, 10, 10]}]
    rows = {r["id"]: r for r in validate.inventory(els, {"bg": {"scale": 1, "x": 0, "y": 0}, "layers": []}, gfa)}
    assert rows["headline"]["status"] == "LOST" and rows["headline"]["ok"] is False
    assert rows["bulb"]["status"] == "DROPPED"


# ---------- 이미지 기반 규칙 ----------

def test_white_ratio_and_forbidden_bg_fail(gfa):
    white = Image.new("RGB", (1250, 560), (255, 255, 255))
    rows = {r["check"]: r["ok"] for r in validate.check_image(white, {"layers": []}, gfa, {})}
    assert rows["흰색 면적"] is False
    assert rows["가장자리 흰색/검정 채움 금지"] is False
    gray = Image.new("RGB", (1250, 560), (242, 242, 242))
    rows = {r["check"]: r["ok"] for r in validate.check_image(gray, {"layers": []}, gfa, {})}
    assert rows["배경색 금지(#F2F2F2 계열)"] is False


def test_normal_dark_image_passes_image_rules(gfa):
    img = Image.new("RGB", (1250, 560), (30, 30, 35))
    rows = validate.check_image(img, {"layers": []}, gfa, {})
    assert all(r["ok"] for r in rows)
