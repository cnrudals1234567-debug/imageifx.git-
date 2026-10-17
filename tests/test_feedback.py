"""피드백 학습: 규칙 검증, 덮어쓰기 격리, 제안→미리보기→반영 흐름, 좋아요 참고. claude·CLI는 가짜."""
import json
import os

import pytest

from test_web import PRESET, TITLE_X, client, env, tokens, upload  # noqa: F401  (fixtures)
from web import feedback, fixer, store, worker


@pytest.fixture
def fb(env, tmp_path, monkeypatch):
    monkeypatch.setattr(feedback, "DIR", str(tmp_path / "feedback"))
    monkeypatch.setattr(worker, "BASELINES", [])
    return env


def test_knob_validation():
    ok, bad = feedback.validate_knobs({"style:text_scale": 0.9, "style:scrim": True, "style:text_side": "left", "design:scrim.alpha": 0.8})
    assert ok == {"style:text_scale": 0.9, "style:scrim": True, "style:text_side": "left", "design:scrim.alpha": 0.8} and bad == []
    ok, bad = feedback.validate_knobs({"style:text_scale": 5, "design:export.jpg_quality_min": 1, "style:text_side": "up",
                                       "style:scrim": "yes", "design:layout.text_width_ratio.safe_zone": "abc"})
    assert ok == {} and len(bad) == 5
    assert feedback.clean_rule({"text": "  ", "knobs": {}}) is None
    r = feedback.clean_rule({"text": "x", "scope": "weird", "applies_to": ["fix", "bogus"], "knobs": {"style:text_scale": 0.8}})
    assert r["scope"] == "global" and r["applies_to"] == ["fix"]


def test_overlay_only_applies_with_env(tmp_path, monkeypatch):
    from imageifx import spec
    monkeypatch.delenv("IMAGEIFX_LEARNED", raising=False)
    base, st = spec.get("scrim.alpha"), spec.style("dark_board").get("text_scale")
    f = tmp_path / "ov.json"
    f.write_text(json.dumps({"design": {"scrim.alpha": 0.5}, "style": {"text_scale": 0.55}}), encoding="utf-8")
    assert spec.get("scrim.alpha") == base and spec.style("dark_board").get("text_scale") == st   # CLI 직접 실행(기준 재현)은 그대로
    monkeypatch.setenv("IMAGEIFX_LEARNED", str(f))
    assert spec.get("scrim.alpha") == 0.5 and spec.style("dark_board")["text_scale"] == 0.55
    monkeypatch.delenv("IMAGEIFX_LEARNED")
    assert spec.get("scrim.alpha") == base


def test_overlay_scopes_and_disable(fb):
    feedback.add_rules([{"text": "a", "scope": "global", "target": "", "applies_to": ["layout"], "knobs": {"style:text_scale": 0.9}},
                        {"text": "b", "scope": "style", "target": "bold_solid", "applies_to": ["analysis"], "knobs": {"design:scrim.alpha": 0.7}},
                        {"text": "c", "scope": "preset", "target": PRESET, "applies_to": ["fix"], "knobs": {"style:person_h_ratio": 0.5}}], "t")
    ov = feedback.overlay_for("bold_solid", PRESET)
    assert ov["style"] == {"text_scale": 0.9, "person_h_ratio": 0.5} and ov["design"] == {"scrim.alpha": 0.7}
    other = feedback.overlay_for("dark_board", "gfa_1250x560")
    assert other["style"] == {"text_scale": 0.9} and other["design"] == {}      # 다른 스타일·사이즈에는 해당 규칙만
    assert feedback.rules_text("analysis") == ["b"] and feedback.rules_text("fix", "dark_board", PRESET) == ["c"]
    rid = feedback.load_rules()[0]["id"]
    feedback.set_enabled(rid, False)
    assert "text_scale" not in feedback.overlay_for("bold_solid", "zzz").get("style", {})
    feedback.delete_rule(rid)
    assert len(feedback.load_rules()) == 2


def _submit(client, jid, text="제목이 너무 작아", scope="all", apply_here="1"):
    r = client.post(f"/jobs/{jid}/size/{PRESET}/feedback", data={"text": text, "scope": scope, "apply_here": apply_here})
    assert r.status_code == 303


def test_propose_preview_accept_apply_and_revert(client, fb):
    jid = upload(client)
    assert store.load(jid)["results"][PRESET]["fails"] == 1
    t0 = tokens(jid)
    _submit(client, jid)
    j = store.load(jid)
    f = j["feedback"][0]
    assert f["status"] == "proposed" and f["rules"][0]["knobs"] == {"style:text_scale": 1.1} and f["preview"]["fails"] == 0
    assert tokens(jid) == t0 + 60 and j["usage"][-1]["kind"] == "feedback"      # 규칙 정리 1회만 토큰
    assert "제목이 너무 작아" in fb.last_feedback_prompt and "style:text_scale" in fb.last_feedback_prompt
    assert feedback.load_rules() == []                                         # 반영 전에는 저장되지 않는다
    page = client.get(f"/jobs/{jid}/size/{PRESET}").text
    assert "규칙 적용 미리보기" in page and "반영" in page
    assert client.post(f"/jobs/{jid}/feedback/1/accept", data={"apply_here": "1"}).status_code == 303
    j = store.load(jid)
    assert len(feedback.load_rules()) == 1 and j["feedback"][0]["status"] == "applied"
    assert j["results"][PRESET]["fails"] == 0 and j["fixes"][-1]["kind"] == "learn"      # 이 이미지에도 적용됨
    assert client.post(f"/jobs/{jid}/size/{PRESET}/revert").status_code == 303           # 되돌릴 수 있다
    assert store.load(jid)["results"][PRESET]["fails"] == 1
    jid2 = upload(client, color=(5, 6, 7))                                              # 이후 업로드에는 저장된 규칙이 적용된다
    r2 = store.load(jid2)["results"][PRESET]
    assert r2["fails"] == 0 and r2["learned"] == [feedback.load_rules()[0]["id"]]


def test_blocked_when_preview_adds_fail(client, fb):
    TITLE_X[0] = 60
    try:
        jid = upload(client)
    finally:
        TITLE_X[0] = 10
    fb.fb_rules = [{"text": "너무 작게", "scope": "global", "applies_to": ["layout"], "knobs": {"style:text_scale": 0.5}}]
    _submit(client, jid)
    f = store.load(jid)["feedback"][0]
    assert f["preview"]["fails"] == 1 and "늘어" in f["blocked"]
    assert client.post(f"/jobs/{jid}/feedback/1/accept").status_code == 409 and feedback.load_rules() == []


def test_rejects_unsafe_knobs_and_keeps_text_rules(client, fb):
    fb.fb_rules = [{"text": "폰트를 바꿔", "scope": "global", "applies_to": ["layout"], "knobs": {"design:export.jpg_quality_min": 1, "style:text_scale": 9}},
                   {"text": "장식은 항상 decor로", "scope": "style", "applies_to": ["analysis"], "knobs": {}}]
    jid = upload(client)
    _submit(client, jid, scope="style")
    f = store.load(jid)["feedback"][0]
    assert f["rules"][0]["knobs"] == {} and len(f["rules"][0]["rejected_knobs"]) == 2    # 허용 밖·범위 밖 수치는 버려진다
    assert f["rules"][1]["scope"] == "style" and f["rules"][1]["target"] == "dark_board"   # 범위는 사용자가 고른 대로
    assert f["preview"] is None
    assert client.post(f"/jobs/{jid}/feedback/1/accept").status_code == 303
    assert "장식은 항상 decor로" in fixer.analyzer_system_prompt()                       # 글 규칙은 분석 프롬프트에 들어간다


def test_baseline_change_needs_extra_confirmation(client, fb, monkeypatch):
    monkeypatch.setattr(worker, "BASELINES", [("tesol_v2_gfa_1250x560", "dark_board")])
    jid = upload(client)
    _submit(client, jid)
    f = store.load(jid)["feedback"][0]
    assert f["baseline_changed"] == ["tesol_v2_gfa_1250x560"]
    assert client.post(f"/jobs/{jid}/feedback/1/accept").status_code == 409                  # 확인 없이는 반영 불가
    assert client.post(f"/jobs/{jid}/feedback/1/accept", data={"confirm_baseline": "1"}).status_code == 303
    assert len(feedback.load_rules()) == 1
    assert not [d for d in os.listdir(store.OUT) if "_bl" in d]                             # 기준 확인용 임시 결과는 지워진다


def test_like_saves_reference_and_feeds_prompts(client, fb):
    jid = upload(client)
    assert client.post(f"/jobs/{jid}/size/{PRESET}/like", data={"note": "배치가 좋다"}).status_code == 303
    liked = feedback.list_liked()
    assert len(liked) == 1 and liked[0]["style"] == "dark_board" and tokens(jid) == 60   # 좋아요는 토큰 0
    assert "사용자가 좋다고 한 결과" in fixer.analyzer_system_prompt()
    assert PRESET in feedback.liked_layout_text("dark_board", PRESET)
    assert client.get("/learning").status_code == 200
    assert client.get(f"/learning/liked/{liked[0]['id']}/image").status_code == 200
    assert client.get("/learning/liked/..%2f..%2fx/image").status_code == 404
    assert client.post(f"/jobs/{jid}/size/{PRESET}/like").status_code == 303 and feedback.list_liked() == []   # 다시 누르면 취소


def test_learning_page_toggle_delete(client, fb):
    feedback.add_rules([{"text": "규칙1", "scope": "global", "target": "", "applies_to": ["fix"], "knobs": {}}], "t")
    rid = feedback.load_rules()[0]["id"]
    assert "규칙1" in client.get("/learning").text
    client.post(f"/learning/rules/{rid}/toggle", data={"on": ""})
    assert feedback.load_rules()[0]["enabled"] is False and feedback.rules_text("fix", "x", "y") == []
    client.post(f"/learning/rules/{rid}/delete")
    assert feedback.load_rules() == []
