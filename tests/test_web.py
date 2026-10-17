"""웹 UI 테스트. claude 호출과 imageifx CLI(LaMa)는 가짜로 대체한다."""
import copy
import io
import json
import os
import sys

import pytest
from PIL import Image

from imageifx import cli
from web import agent, autotune, fixer, ops, store, worker

PRESET = "meta_1080x1080"
TITLE_X = [10]   # 가짜 CLI가 만드는 제목 x. 10이면 안전영역(48) 밖이라 FAIL, 60이면 PASS


# ---- 가짜 CLI: layout.json 내용에 따라 결과 이미지·report가 결정된다 ----
def _write_files(wd):
    lay = ops.read_layout(wd)
    pos = [(L["id"], L["x"], L["y"], L["scale"]) for L in lay["layers"]]
    h = abs(hash(json.dumps(pos))) % 200
    Image.new("RGB", (100, 50), (h, 50, 80)).save(os.path.join(wd, lay["name"] + ".png"))
    for fn in ("debug.png", "compare.png"):
        Image.new("RGB", (40, 40)).save(os.path.join(wd, fn))
    with open(os.path.join(wd, "elements.json"), encoding="utf-8") as f:
        els = json.load(f)["elements"]
    title = next(L for L in lay["layers"] if L["id"] == "title")
    rows = [{"check": "안전영역 글자 [title]", "ok": title["x"] >= 48, "detail": f"x={title['x']}"}]
    if any(e["id"] == "chalk" and e["importance"] == "movable" for e in els):
        rows.append({"check": "요소 보존 [chalk] movable", "ok": False, "detail": "LOST - 자리 없음"})
    with open(os.path.join(wd, "report.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False)
    return 0 if all(r["ok"] for r in rows) else 1


def _make(wd, name, preset, elements, learned=None):
    os.makedirs(wd, exist_ok=True)
    tx = TITLE_X[0]
    if learned:   # 학습 덮어쓰기: text_scale 1.1 -> x=66(PASS), 0.5 -> x=30(안전영역 48 밖, FAIL)
        with open(learned, encoding="utf-8") as f:
            ts = json.load(f).get("style", {}).get("text_scale")
        if ts is not None:
            tx = int(ts * 60)
    layers = [{"id": "title", "type": "text", "importance": "critical", "src_bbox": [0, 0, 100, 40], "scale": 1.0, "x": tx, "y": 100},
              {"id": "owl", "type": "subject", "importance": "critical", "src_bbox": [0, 0, 200, 200], "scale": 1.0, "x": 300, "y": 600}]
    with open(os.path.join(wd, "layout.json"), "w", encoding="utf-8") as f:
        json.dump({"bg": {"scale": 1, "x": 0, "y": 0}, "layers": layers, "preset": preset, "name": name, "canvas": [1080, 1080]}, f)
    with open(os.path.join(wd, "elements.json"), "w", encoding="utf-8") as f:
        json.dump({"elements": elements, "src_size": [400, 300], "style": "dark_board"}, f)
    for L in layers:
        Image.new("RGBA", (50, 20), (255, 255, 255, 255)).save(os.path.join(wd, f"layer_{L['id']}.png"))
    Image.new("RGB", (10, 10)).save(os.path.join(wd, "clean_bg.png"))


def fake_cli_factory(log):
    def fake_cli(args, learned=None):
        log.append(args[0])
        pr = args[args.index("--preset") + 1] if "--preset" in args else None
        if args[0] == "run":
            jid = os.path.splitext(os.path.basename(args[1]))[0]
            name = f"{jid}_{pr}"
            with open(args[2], encoding="utf-8") as f:
                els = json.load(f)["elements"]
            _make(os.path.join(store.OUT, name), name, pr, els, learned)
            return _write_files(os.path.join(store.OUT, name)), "출력: x"
        if args[0] == "relayout":
            base, tag = args[1], args[args.index("--tag") + 1]
            old = ops.read_layout(base)
            name = f"{old['name']}_{tag}"
            with open(os.path.join(base, "elements.json"), encoding="utf-8") as f:
                els = json.load(f)["elements"]
            wd = os.path.join(store.OUT, name)
            _make(wd, name, pr or old["preset"], els, learned)
            return _write_files(wd), "출력: x"
        return _write_files(args[1]), "출력: x"   # render
    return fake_cli


@pytest.fixture
def env(tmp_path, monkeypatch):
    out = tmp_path / "output"
    monkeypatch.setattr(store, "OUT", str(out))
    monkeypatch.setattr(store, "JOBS", str(out / "_jobs"))
    monkeypatch.setattr(store, "INPUT", str(tmp_path / "input"))
    monkeypatch.setattr(store, "USAGE", str(out / "_usage.jsonl"))
    monkeypatch.setattr(store, "CACHE", str(out / "_analysis_cache"))

    class E:
        calls = []
        cli_calls = []
        bad_first = False
        ai_ops = [{"op": "move", "group": "text", "dx": 60, "dy": 0}]
        elements = [{"id": "title", "type": "text", "importance": "critical", "bbox": [10, 10, 200, 80]}]
        fb_rules = [{"text": "제목을 더 크게", "scope": "global", "applies_to": ["layout"], "knobs": {"style:text_scale": 1.1}}]
        last_feedback_prompt = ""
    e = E()
    e.calls, e.cli_calls = [], []

    def fake_claude(prompt, **kw):
        usage = {"input": 10, "output": 20, "cache_read": 1000, "cache_creation": 30}
        if "사용자 피드백" in prompt:   # 피드백 학습 에이전트
            e.calls.append("feedback")
            e.last_feedback_prompt = prompt
            return {"text": "```json\n" + json.dumps({"rules": e.fb_rules, "notes": "정리함"}) + "\n```", "usage": usage, "cost": 0.1,
                    "ms": 10, "turns": 2, "is_error": False, "aborted": False}
        if "source.png" in prompt:   # 분석
            e.calls.append("analyze")
            data = {"style": "dark_board", "elements": e.elements}
            if len(e.calls) == 1 and e.bad_first:
                data = {"style": "nope", "elements": e.elements}
            text = "```json\n" + json.dumps(data) + "\n```"
        else:                          # AI 수정
            e.calls.append("fix")
            assert kw["lean"] and kw["tools"] == ["Read"] and "--bare" not in str(kw)
            text = "```json\n" + json.dumps({"ops": e.ai_ops, "notes": "글자를 옮김"}) + "\n```"
        return {"text": text, "usage": usage, "cost": 0.5, "ms": 100, "turns": 2, "is_error": False, "aborted": False}

    cli_fn = fake_cli_factory(e.cli_calls)
    monkeypatch.setattr(worker, "claude", fake_claude)
    monkeypatch.setattr(worker, "run_cli", cli_fn)
    monkeypatch.setattr(ops, "run_cli", cli_fn)

    def run_sync(t):   # 실제 워커 루프(_loop)와 같이 작업 단위로 예외를 가둔다
        try:
            {"analyze": worker.do_analyze, "convert": worker.do_convert, "fix": worker.do_fix, "feedback": worker.do_feedback}[t["kind"]](t)
        except Exception as ex:
            import traceback
            traceback.print_exc()
            if t["kind"] == "feedback":
                worker._fail_feedback(t["job"], t["fid"], f"{type(ex).__name__}: {ex}")
            else:
                worker._fail(t["job"], f"{type(ex).__name__}: {ex}", fix_n=t.get("n"))
    monkeypatch.setattr(worker, "enqueue", run_sync)
    e.real_auto = worker._auto_tune_new
    monkeypatch.setattr(worker, "_auto_tune_new", lambda jid, presets: None)   # 기본은 끔(처음 FAIL 상태를 검증하기 위해)
    e.monkeypatch = monkeypatch
    return e


@pytest.fixture
def client(env):
    from fastapi.testclient import TestClient
    from web.app import app
    return TestClient(app, follow_redirects=False)


def png_bytes(size=(400, 300), color=(30, 30, 30)):
    b = io.BytesIO()
    Image.new("RGB", size, color).save(b, "PNG")
    return b.getvalue()


def upload(client, presets=(PRESET,), confirm="", color=(30, 30, 30)):
    r = client.post("/jobs", files={"image": ("a.png", png_bytes(color=color), "image/png")}, data={"presets": list(presets), "confirm": confirm})
    assert r.status_code == 303
    return r.headers["location"].rsplit("/", 1)[1]


def tokens(jid):
    return store.sum_usage(store.load(jid)["usage"])["total"]


# ---- 사이즈 카탈로그 ----
def test_catalog_metadata():
    ps = cli._presets()
    for k, p in ps.items():
        assert p["size"] and p["mode"] in ("gfa", "free"), k
        assert p.get("group") in ("gfa", "meta", "google"), k
        for key in ("label", "use", "grade", "note"):
            assert p.get(key), (k, key)
        assert "verified" in p
    shown = [k for k, p in ps.items() if not p.get("hidden")]
    assert "gfa_1250x560" in shown and "google_900x1600" in shown
    assert "gfa_1200x560" not in shown and "google_1080x1920" not in shown and "meta_1080x1920" not in shown   # 메타 스토리·릴스는 사용자 결정으로 제외
    assert ps["meta_1080x1920"]["safe_zone"]["top"] > 0


def test_index_groups(client):
    t = client.get("/").text
    assert t.index("네이버 GFA") < t.index("메타(페이스북") < t.index("구글")
    assert "meta_1080x1350" in t and "gfa_1200x560" not in t and "공식 미확인" in t


# ---- 순수 함수 ----
def test_validate_analysis_and_scale():
    ok = {"style": "dark_board", "elements": [{"id": "a", "type": "text", "importance": "critical", "bbox": [0, 0, 10, 10]}]}
    assert fixer.validate_analysis(ok, (100, 100)) == []
    bad = {"style": "x", "elements": [{"id": "a", "type": "text", "importance": "huge", "bbox": [0, 0, 500, 10]}]}
    assert len(fixer.validate_analysis(bad, (100, 100))) >= 4
    d = fixer.scale_analysis({"elements": [{"bbox": [10, 10, 20, 20]}]}, 2.0, (100, 100))
    assert d["elements"][0]["bbox"] == [18, 18, 42, 42]


def test_extract_json_block_and_usage():
    assert agent.extract_json_block('x\n```json\n{"a": 1}\n```') == {"a": 1}
    assert agent.extract_json_block('앞 {"b": 2}') == {"b": 2}
    assert agent.extract_json_block("없음") is None
    u = agent.parse_usage({"input_tokens": 1, "output_tokens": 2, "cache_read_input_tokens": 3, "cache_creation_input_tokens": 4})
    assert u == {"input": 1, "output": 2, "cache_read": 3, "cache_creation": 4} and agent.billable(u) == 7


def test_build_cmd_is_lean():
    c = agent.build_cmd("claude", system_prompt="s", tools=["Read"], model="sonnet", effort="low", lean=True, max_budget_usd=1)
    for f in ("--strict-mcp-config", "--no-session-persistence", "--disable-slash-commands", "--system-prompt", "--model"):
        assert f in c
    assert "--bare" not in c and c[c.index("--tools") + 1] == "Read"
    assert agent.build_cmd("claude", tools="")[-2:] == ["--tools", ""]


def _fake_exe(tmp_path, body):
    script = tmp_path / "fake.py"
    script.write_text("import json,sys\nsys.stdin.read()\n" + body, encoding="utf-8")
    exe = tmp_path / "claude.cmd"
    exe.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    return str(exe)


def test_run_claude_parses_stream(tmp_path):
    exe = _fake_exe(tmp_path,
                    "print(json.dumps({'type':'assistant','message':{'id':'m1','content':[{'type':'tool_use','name':'Read','input':{'file_path':'a.png'}}]}}))\n"
                    "print(json.dumps({'type':'result','result':'완료','usage':{'input_tokens':5,'output_tokens':6,'cache_read_input_tokens':7,'cache_creation_input_tokens':8},"
                    "'total_cost_usd':0.1,'duration_ms':9,'num_turns':2,'is_error':False}))\n")
    seen = []
    r = agent.run_claude("p", on_progress=seen.append, claude_bin=exe)
    assert r["usage"] == {"input": 5, "output": 6, "cache_read": 7, "cache_creation": 8} and r["text"] == "완료" and seen == ["Read a.png"]
    assert not r["aborted"]


def test_run_claude_aborts_over_token_cap(tmp_path):
    exe = _fake_exe(tmp_path,
                    "for i in range(3):\n"
                    "    print(json.dumps({'type':'assistant','message':{'id':'m%d'%i,'usage':{'input_tokens':100,'output_tokens':5000,'cache_creation_input_tokens':0,'cache_read_input_tokens':9},'content':[]}}),flush=True)\n"
                    "print(json.dumps({'type':'result','result':'끝','usage':{}}))\n")
    r = agent.run_claude("p", claude_bin=exe, max_tokens=6000)
    assert r["aborted"] and r["usage"]["output"] >= 5000   # 상한 초과 시 끊고, 그때까지의 사용량은 돌려준다


# ---- 배치 연산 ----
def _layout():
    return {"layers": [{"id": "t1", "type": "text", "importance": "critical", "src_bbox": [0, 0, 100, 40], "scale": 1.0, "x": 100, "y": 100},
                       {"id": "t2", "type": "text", "importance": "critical", "src_bbox": [0, 0, 100, 40], "scale": 1.0, "x": 100, "y": 160},
                       {"id": "owl", "type": "subject", "importance": "critical", "src_bbox": [0, 0, 200, 200], "scale": 1.0, "x": 300, "y": 400}]}


def test_ops_move_scale_scrim():
    lay = _layout()
    ops.apply_op(lay, {"op": "move", "group": "text", "dx": 10, "dy": -20})
    assert (lay["layers"][0]["x"], lay["layers"][0]["y"], lay["layers"][1]["y"]) == (110, 80, 140)
    ops.apply_op(lay, {"op": "scale", "group": "subject", "factor": 0.5})
    owl = lay["layers"][2]
    assert owl["scale"] == 0.5 and owl["y"] + 100 == 600   # 바닥(600)이 그대로
    ops.apply_op(lay, {"op": "scrim", "on": True})
    assert "scrim" in lay
    ops.apply_op(lay, {"op": "scrim", "on": False})
    assert "scrim" not in lay
    for bad in ({"op": "move", "id": "nope", "dx": 1}, {"op": "scale", "group": "text", "factor": 9}, {"op": "move", "group": "text", "dx": 999}, {"op": "x"}):
        with pytest.raises(ValueError):
            ops.apply_op(lay, bad)


def test_text_block_scale_is_uniform():
    lay = _layout()
    ops.apply_op(lay, {"op": "scale", "group": "text", "factor": 0.8})
    assert {L["scale"] for L in lay["layers"] if L["type"] == "text"} == {0.8}   # 글자는 균등 배율만


def test_autotune_fixes_safe_zone():
    preset = cli._presets()[PRESET]
    lay = _layout()
    lay["layers"][0]["x"] = 10   # 안전영역(48) 밖
    new, desc, before, after = autotune.solve(lay, preset, {})
    assert before >= 1 and after == 0 and new["layers"][0]["x"] >= 48
    assert new["layers"][0]["scale"] == 1.0 or new["layers"][0]["scale"] < 1.0   # 위치와 균등 배율만 바뀜
    ok_lay = _layout()
    assert autotune.solve(ok_lay, preset, {})[0] is None


def test_parse_ai_ops_rejects_critical_drop():
    lay = _layout()
    els = [{"id": "t1", "importance": "critical"}, {"id": "chalk", "importance": "movable"}]
    good, drops, bad, notes = fixer.parse_ai_ops(
        {"ops": [{"op": "drop", "id": "t1"}, {"op": "drop", "id": "chalk"}, {"op": "move", "id": "ghost", "dx": 1}, {"op": "move", "id": "t1", "dx": 5}], "notes": "n"},
        lay, els)
    assert drops == ["chalk"] and len(good) == 1 and len(bad) == 2 and notes == "n"


# ---- 웹 흐름 ----
def test_upload_validation(client):
    assert client.post("/jobs", files={"image": ("a.txt", b"hello", "text/plain")}, data={"presets": [PRESET]}).status_code == 400
    assert client.post("/jobs", files={"image": ("a.png", png_bytes(), "image/png")}, data={"presets": ["nope"]}).status_code == 400
    assert client.post("/jobs", files={"image": ("a.png", png_bytes((50, 50)), "image/png")}, data={"presets": [PRESET]}).status_code == 400


def test_full_flow_pages_and_usage(client, env):
    jid = upload(client, presets=("gfa_1250x560", PRESET, "google_900x1600"))
    j = store.load(jid)
    assert j["status"] == "done" and len(j["results"]) == 3
    assert env.cli_calls == ["run", "relayout", "relayout"]   # LaMa 분해는 첫 사이즈만
    page = client.get(f"/jobs/{jid}").text
    assert "네이버 GFA" in page and "메타" in page and "/size/meta_1080x1080" in page and "사이즈 추가" in page
    sp = client.get(f"/jobs/{jid}/size/{PRESET}").text
    assert "빠른 조정" in sp and "자동 보정" in sp and "AI 수정 요청" in sp and "최종본이 아닙니다" in sp
    assert client.get(f"/jobs/{jid}/size/{PRESET}/fragment").status_code == 200
    assert client.get(f"/jobs/{jid}/size/not_a_preset").status_code == 404
    assert tokens(jid) == 60 and "60" in client.get("/usage").text   # 분석 1회: 10+20+30


def test_analysis_retry_cache_and_cap(client, env):
    env.bad_first = True
    jid = upload(client, color=(1, 2, 3))
    assert store.load(jid)["status"] == "done" and len(store.load(jid)["usage"]) == 2   # 검증 실패 -> 재시도, 둘 다 기록
    n = len(env.calls)
    jid2 = upload(client, color=(1, 2, 3))                # 같은 이미지: 분석 캐시 -> 호출 0
    assert len(env.calls) == n and store.load(jid2)["usage"] == [] and store.load(jid2)["status"] == "done"


def test_confirm_waits_then_starts(client, env):
    jid = upload(client, confirm="1")
    assert store.load(jid)["status"] == "awaiting_confirm"
    assert client.post(f"/jobs/{jid}/start").status_code == 303 and store.load(jid)["status"] == "done"
    assert client.post(f"/jobs/{jid}/start").status_code == 409


def test_add_sizes_costs_no_tokens(client, env):
    jid = upload(client)
    t0 = tokens(jid)
    assert client.post(f"/jobs/{jid}/add-sizes", data={"presets": [PRESET, "meta_1080x1350"]}).status_code == 303
    j = store.load(jid)
    assert set(j["results"]) == {PRESET, "meta_1080x1350"} and tokens(jid) == t0
    assert env.cli_calls == ["run", "relayout"] and j["status"] == "done"
    assert client.post(f"/jobs/{jid}/add-sizes", data={"presets": [PRESET]}).status_code == 400   # 이미 있는 사이즈


def test_quick_merge_and_revert_leave_original_untouched(client, env):
    jid = upload(client)
    r0 = store.load(jid)["results"][PRESET]
    orig_md5 = fixer_md5(store.ab(r0["final"]))
    t0 = tokens(jid)
    assert client.post(f"/jobs/{jid}/size/{PRESET}/quick", data={"action": "text_right"}).status_code == 303
    j = store.load(jid)
    r1 = j["results"][PRESET]
    assert r1["workdir"] != r0["workdir"] and j["fixes"][0]["kind"] == "quick" and j["fixes"][0]["status"] == "done"
    assert ops.read_layout(store.ab(r1["workdir"]))["layers"][0]["x"] == 26
    client.post(f"/jobs/{jid}/size/{PRESET}/quick", data={"action": "text_right"})   # 이어서 누르면 같은 폴더·같은 기록
    j = store.load(jid)
    assert len(j["fixes"]) == 1 and j["results"][PRESET]["workdir"] == r1["workdir"]
    assert ops.read_layout(store.ab(r1["workdir"]))["layers"][0]["x"] == 42
    assert client.post(f"/jobs/{jid}/size/{PRESET}/revert").status_code == 303
    j = store.load(jid)
    assert j["results"][PRESET]["workdir"] == r0["workdir"] and fixer_md5(store.ab(j["results"][PRESET]["final"])) == orig_md5
    assert tokens(jid) == t0 and client.post(f"/jobs/{jid}/size/{PRESET}/revert").status_code == 409
    assert client.post(f"/jobs/{jid}/size/{PRESET}/quick", data={"action": "bogus"}).status_code == 400


def fixer_md5(path):
    import hashlib
    return hashlib.md5(open(path, "rb").read()).hexdigest()


def test_auto_fix_is_free_and_clears_fail(client, env):
    jid = upload(client)
    assert store.load(jid)["results"][PRESET]["fails"] == 1
    t0 = tokens(jid)
    assert client.post(f"/jobs/{jid}/size/{PRESET}/auto").status_code == 303
    j = store.load(jid)
    assert j["results"][PRESET]["fails"] == 0 and j["fixes"][0]["kind"] == "auto" and tokens(jid) == t0 and env.calls == ["analyze"]
    # 이미 FAIL이 없으면 할 일이 없다고 실패로 기록하되 작업은 그대로 유효
    client.post(f"/jobs/{jid}/size/{PRESET}/auto")
    j = store.load(jid)
    assert j["status"] == "done" and j["fixes"][-1]["status"] == "failed" and j["results"][PRESET]["fails"] == 0


def test_ai_fix_uses_tokens_and_limits(client, env, monkeypatch):
    jid = upload(client)
    t0 = tokens(jid)
    assert client.post(f"/jobs/{jid}/size/{PRESET}/fix", data={"request": "글자를 오른쪽으로"}).status_code == 303
    j = store.load(jid)
    fx = j["fixes"][0]
    assert fx["status"] == "done" and fx["kind"] == "ai" and fx["notes"] == "글자를 옮김" and j["results"][PRESET]["fails"] == 0
    assert tokens(jid) == t0 + 60 and [u["kind"] for u in j["usage"]] == ["analyze", "fix"]
    assert "AI 수정 요청" in client.get(f"/jobs/{jid}/size/{PRESET}").text
    monkeypatch.setattr(worker, "MAX_AI_FIX_PER_SIZE", 1)
    assert client.post(f"/jobs/{jid}/size/{PRESET}/fix", data={"request": "또"}).status_code == 409
    assert client.post(f"/jobs/{jid}/size/{PRESET}/quick", data={"action": "text_up"}).status_code == 303   # 무료 수정은 계속 가능


def test_daily_limit_blocks_ai_only(client, env, monkeypatch):
    jid = upload(client)
    monkeypatch.setattr(worker, "DAILY_TOKEN_LIMIT", 1)
    r = client.post(f"/jobs/{jid}/size/{PRESET}/fix", data={"request": "x"})
    assert r.status_code == 409 and "상한" in r.json()["detail"]
    assert client.post(f"/jobs/{jid}/size/{PRESET}/auto").status_code == 303


def test_sizes_are_fixed_independently(client, env):
    jid = upload(client, presets=(PRESET, "meta_1080x1350"))
    client.post(f"/jobs/{jid}/size/{PRESET}/quick", data={"action": "text_right"})
    j = store.load(jid)
    assert j["results"]["meta_1080x1350"]["workdir"].endswith("meta_1080x1350") and [fx["preset"] for fx in j["fixes"]] == [PRESET]
    client.post(f"/jobs/{jid}/size/meta_1080x1350/auto")
    assert sorted(fx["preset"] for fx in store.load(jid)["fixes"]) == sorted([PRESET, "meta_1080x1350"])


def test_drop_lost_decor_button(client, env):
    env.elements = env.elements + [{"id": "chalk", "type": "decor", "importance": "movable", "bbox": [100, 100, 150, 120]}]
    jid = upload(client)
    page = client.get(f"/jobs/{jid}/size/{PRESET}").text
    assert "chalk" in page and "장식 빼기" in page
    assert client.post(f"/jobs/{jid}/size/{PRESET}/quick", data={"action": "drop", "element": "chalk"}).status_code == 303
    j = store.load(jid)
    assert j["fixes"][0]["status"] == "done"
    with open(os.path.join(store.ab(j["results"][PRESET]["workdir"]), "elements.json"), encoding="utf-8") as f:
        assert next(e for e in json.load(f)["elements"] if e["id"] == "chalk")["importance"] == "drop"
    # 필수 요소는 빼지 못한다(quick drop은 장식만)
    client.post(f"/jobs/{jid}/size/{PRESET}/quick", data={"action": "drop", "element": "title"})
    with open(os.path.join(store.ab(store.load(jid)["results"][PRESET]["workdir"]), "elements.json"), encoding="utf-8") as f:
        assert next(e for e in json.load(f)["elements"] if e["id"] == "title")["importance"] == "critical"


def test_file_access_is_confined(client, env, tmp_path):
    jid = upload(client)
    res = store.load(jid)["results"][PRESET]
    assert client.get(f"/jobs/{jid}/files/{res['final']}").status_code == 200
    (tmp_path / "output" / "secret.json").write_text("{}")
    other = upload(client, color=(9, 9, 9))
    assert client.get(f"/jobs/{jid}/files/secret.json").status_code == 404
    assert client.get(f"/jobs/{jid}/files/../secret.json").status_code == 404
    assert client.get(f"/jobs/{jid}/files/_jobs/{other}/job.json").status_code == 404
    assert client.get("/jobs/..%2f..%2fx").status_code == 404


def test_password_required(client, monkeypatch):
    monkeypatch.setenv("IMAGEIFX_WEB_PASSWORD", "pw")
    assert client.get("/").status_code == 401
    assert client.get("/", auth=("u", "pw")).status_code == 200


def test_confirm_style_change_and_reanalyze(client, env):
    jid = upload(client, confirm="1", color=(7, 7, 7))
    assert "스타일" in client.get(f"/jobs/{jid}").text and store.load(jid)["style"] == "dark_board"
    assert client.post(f"/jobs/{jid}/start", data={"style": "nope"}).status_code == 400
    n = len(env.calls)
    assert client.post(f"/jobs/{jid}/reanalyze", data={"note": "배경은 단색"}).status_code == 303   # 캐시 무시, 토큰 사용
    assert len(env.calls) == n + 1 and store.load(jid)["status"] == "awaiting_confirm"
    assert client.post(f"/jobs/{jid}/start", data={"style": "bold_solid"}).status_code == 303
    j = store.load(jid)
    assert j["style"] == "bold_solid" and j["status"] == "done"
    with open(os.path.join(store.job_dir(jid), "analysis.json"), encoding="utf-8") as f:
        assert json.load(f)["style"] == "bold_solid"
    assert client.post(f"/jobs/{jid}/reanalyze").status_code == 409   # 변환 뒤에는 불가


def test_analyzer_prompt_uses_style_docs_and_examples():
    t = fixer.analyzer_system_prompt()
    assert "bold_solid" in t and "dark_board" in t and "{{" not in t and "예시 1" in t
    assert "예시" not in fixer.analyzer_system_prompt(exclude=fixer.EXAMPLE_FILES).split("## 참고 예시")[1].replace("(사람이 검증한 분석, 다른 소재다)", "")


def test_auto_tune_runs_after_convert(client, env):
    env.monkeypatch.setattr(worker, "_auto_tune_new", env.real_auto)
    jid = upload(client, presets=(PRESET, "meta_1080x1350"))
    j = store.load(jid)
    assert j["status"] == "done" and tokens(jid) == 60 and env.calls == ["analyze"]   # 토큰은 분석 1회뿐
    assert all(r["fails"] == 0 for r in j["results"].values())
    assert [(fx["kind"], fx["status"], fx["label"]) for fx in j["fixes"]] == [("auto", "done", "자동 보정(변환 직후 자동)")] * 2
    # 자동으로 한 보정도 되돌릴 수 있다
    assert client.post(f"/jobs/{jid}/size/{PRESET}/revert").status_code == 303
    assert store.load(jid)["results"][PRESET]["fails"] == 1


def test_auto_tune_skips_clean_results(client, env, monkeypatch):
    env.monkeypatch.setattr(worker, "_auto_tune_new", env.real_auto)
    monkeypatch.setattr(autotune, "solve", lambda *a, **k: pytest.fail("FAIL이 없는 결과에는 자동 보정을 부르면 안 된다"))
    TITLE_X[0] = 60
    try:
        jid = upload(client)
    finally:
        TITLE_X[0] = 10
    j = store.load(jid)
    assert j["results"][PRESET]["fails"] == 0 and j["fixes"] == []
