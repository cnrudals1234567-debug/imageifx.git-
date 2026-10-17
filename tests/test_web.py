"""웹 UI 테스트. claude 호출과 imageifx CLI(LaMa)는 가짜로 대체한다."""
import io
import json
import os
import sys

import pytest
from PIL import Image

from web import agent, fixer, store, worker


@pytest.fixture
def env(tmp_path, monkeypatch):
    out = tmp_path / "output"
    monkeypatch.setattr(store, "OUT", str(out))
    monkeypatch.setattr(store, "JOBS", str(out / "_jobs"))
    monkeypatch.setattr(store, "INPUT", str(tmp_path / "input"))
    monkeypatch.setattr(store, "USAGE", str(out / "_usage.jsonl"))
    calls = []

    class E:
        bad_first = False
    e = E()

    def fake_claude(prompt, agent_name, tools, on_progress=None, **kw):
        calls.append(agent_name)
        usage = {"input": 10, "output": 20, "cache_read": 1000, "cache_creation": 30}
        if agent_name == worker.ANALYZER:
            path = prompt.split("`")[3]
            data = {"style": "dark_board", "elements": [
                {"id": "title", "type": "text", "importance": "critical", "bbox": [10, 10, 200, 80]}]}
            if len([c for c in calls if c == worker.ANALYZER]) == 1 and e.bad_first:
                data["style"] = "nope"
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f)
            return {"text": "저장함", "usage": usage, "cost": 0.5, "ms": 100, "turns": 3, "is_error": False}
        # 수정 에이전트: 최종 이미지를 제자리에서 고치고(색 변경) 보고 JSON을 낸다
        wd = prompt.split("작업 폴더: output/")[1].split()[0]
        final = worker.find_final(os.path.join(store.OUT, wd))
        Image.new("RGB", (100, 50), (0, 255, 0)).save(final)
        with open(os.path.join(store.OUT, wd, "report.json"), "w", encoding="utf-8") as f:
            json.dump([{"check": "안전영역", "ok": True, "detail": "ok"}], f)
        rep = {"final_workdir": wd, "changes": [{"id": "title", "field": "x", "before": 1, "after": 2, "why": "t"}],
               "remaining_fail": [], "notes": "고침"}
        return {"text": "끝\n```json\n" + json.dumps(rep) + "\n```", "usage": usage, "cost": 0.7, "ms": 200,
                "turns": 4, "is_error": False}

    def fake_cli(args):
        if args[0] == "run":
            jid, preset = os.path.splitext(os.path.basename(args[1]))[0], args[args.index("--preset") + 1]
            name = f"{jid}_{preset}"
        else:
            base, preset = args[1], args[args.index("--preset") + 1]
            with open(os.path.join(base, "layout.json"), encoding="utf-8") as f:
                name = json.load(f)["name"] + "_" + preset
        wd = os.path.join(store.OUT, name)
        os.makedirs(wd, exist_ok=True)
        with open(os.path.join(wd, "layout.json"), "w", encoding="utf-8") as f:
            json.dump({"name": name, "preset": preset}, f)
        Image.new("RGB", (100, 50), (255, 0, 0)).save(os.path.join(wd, name + ".png"))
        for fn in ("debug.png", "compare.png"):
            Image.new("RGB", (10, 10)).save(os.path.join(wd, fn))
        with open(os.path.join(wd, "report.json"), "w", encoding="utf-8") as f:
            json.dump([{"check": "안전영역", "ok": False, "detail": "벗어남"}], f)
        return 1, "출력: x"

    monkeypatch.setattr(worker, "claude", fake_claude)
    monkeypatch.setattr(worker, "run_cli", fake_cli)
    monkeypatch.setattr(worker, "enqueue", lambda t: {"analyze": worker.do_analyze, "convert": worker.do_convert, "fix": worker.do_fix}[t["kind"]](t))
    e.calls = calls
    return e


@pytest.fixture
def client(env):
    from fastapi.testclient import TestClient
    from web.app import app
    return TestClient(app, follow_redirects=False)


def png_bytes(size=(400, 300)):
    b = io.BytesIO()
    Image.new("RGB", size, (30, 30, 30)).save(b, "PNG")
    return b.getvalue()


def upload(client, presets=("gfa_1250x560",), confirm=""):
    r = client.post("/jobs", files={"image": ("a.png", png_bytes(), "image/png")},
                    data={"presets": list(presets), "confirm": confirm})
    assert r.status_code == 303
    return r.headers["location"].rsplit("/", 1)[1]


# ---- 순수 함수 ----
def test_validate_analysis():
    ok = {"style": "dark_board", "elements": [{"id": "a", "type": "text", "importance": "critical", "bbox": [0, 0, 10, 10]}]}
    assert fixer.validate_analysis(ok, (100, 100)) == []
    bad = {"style": "x", "elements": [{"id": "a", "type": "text", "importance": "huge", "bbox": [0, 0, 500, 10]}]}
    errs = fixer.validate_analysis(bad, (100, 100))
    assert len(errs) >= 4
    assert fixer.validate_analysis({"style": "dark_board", "elements": []}, (1, 1))


def test_extract_json_block_and_usage():
    assert agent.extract_json_block('x\n```json\n{"a": 1}\n```') == {"a": 1}
    assert agent.extract_json_block("앞 {\"b\": 2}") == {"b": 2}
    assert agent.extract_json_block("없음") is None
    u = agent.parse_usage({"usage": {"input_tokens": 1, "output_tokens": 2, "cache_read_input_tokens": 3, "cache_creation_input_tokens": 4}})
    assert u == {"input": 1, "output": 2, "cache_read": 3, "cache_creation": 4}


def test_run_claude_parses_stream(tmp_path):
    script = tmp_path / "fake.py"
    script.write_text(
        "import json,sys\nsys.stdin.read()\n"
        "print(json.dumps({'type':'assistant','message':{'content':[{'type':'tool_use','name':'Read','input':{'file_path':'a.png'}}]}}))\n"
        "print(json.dumps({'type':'result','result':'완료','usage':{'input_tokens':5,'output_tokens':6,'cache_read_input_tokens':7,'cache_creation_input_tokens':8},"
        "'total_cost_usd':0.1,'duration_ms':9,'num_turns':2,'is_error':False}))\n", encoding="utf-8")
    exe = tmp_path / "claude.cmd"
    exe.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    seen = []
    r = agent.run_claude("프롬프트", "x", ["Read"], on_progress=seen.append, claude_bin=str(exe))
    assert r["usage"] == {"input": 5, "output": 6, "cache_read": 7, "cache_creation": 8}
    assert r["text"] == "완료" and r["turns"] == 2 and seen == ["Read a.png"]


# ---- 웹 흐름 ----
def test_upload_validation(client):
    assert client.post("/jobs", files={"image": ("a.txt", b"hello", "text/plain")}, data={"presets": ["gfa_1250x560"]}).status_code == 400
    assert client.post("/jobs", files={"image": ("a.png", png_bytes(), "image/png")}, data={"presets": ["nope"]}).status_code == 400
    assert client.post("/jobs", files={"image": ("a.png", png_bytes((50, 50)), "image/png")}, data={"presets": ["gfa_1250x560"]}).status_code == 400


def test_full_flow_and_usage(client, env):
    jid = upload(client, presets=("gfa_1250x560", "google_1200x628"))
    j = store.load(jid)
    assert j["status"] == "done" and set(j["results"]) == {"gfa_1250x560", "google_1200x628"}
    assert j["results"]["gfa_1250x560"]["fails"] == 1
    assert os.path.exists(os.path.join(store.job_dir(jid), "analysis_preview.png"))
    page = client.get(f"/jobs/{jid}")
    assert page.status_code == 200 and "최종본이 아닙니다" in page.text and "수정 요청" in page.text and "토큰 사용량" in page.text
    assert client.get(f"/jobs/{jid}/fragment").status_code == 200
    assert client.get("/jobs").status_code == 200 and client.get("/").status_code == 200
    # 사용량: 분석 1회 = 입력10+출력20+캐시생성30 = 60
    assert store.sum_usage(j["usage"])["total"] == 60
    assert "60" in client.get("/usage").text


def test_analysis_retry_on_invalid(client, env):
    env.bad_first = True
    jid = upload(client)
    j = store.load(jid)
    assert j["status"] == "done" and len(j["usage"]) == 2   # 첫 분석이 검증 실패 -> 재시도, 토큰은 두 번 다 기록


def test_confirm_waits_then_starts(client, env):
    jid = upload(client, confirm="1")
    assert store.load(jid)["status"] == "awaiting_confirm"
    assert client.post(f"/jobs/{jid}/start").status_code == 303
    assert store.load(jid)["status"] == "done"
    assert client.post(f"/jobs/{jid}/start").status_code == 409


def test_fix_and_revert_restores_bytes(client, env):
    jid = upload(client)
    res = store.load(jid)["results"]["gfa_1250x560"]
    final = store.ab(res["final"])
    before_md5 = fixer.file_md5(final)
    r = client.post(f"/jobs/{jid}/fix", data={"preset": "gfa_1250x560", "request": "로고가 잘려요"})
    assert r.status_code == 303
    j = store.load(jid)
    fx = j["fixes"][0]
    assert fx["status"] == "done" and fx["report"]["notes"] == "고침"
    assert j["results"]["gfa_1250x560"]["fails"] == 0 and fx["before"]["fails"] == 1
    assert fixer.file_md5(final) != before_md5
    assert len(j["usage"]) == 2 and j["usage"][1]["kind"] == "fix"
    page = client.get(f"/jobs/{jid}").text
    assert "이전 버전으로 되돌리기" in page and "로고가 잘려요" in page
    assert client.post(f"/jobs/{jid}/revert", data={"preset": "gfa_1250x560", "n": 1}).status_code == 303
    assert fixer.file_md5(final) == before_md5
    assert store.load(jid)["results"]["gfa_1250x560"]["fails"] == 1
    assert client.post(f"/jobs/{jid}/revert", data={"preset": "gfa_1250x560", "n": 1}).status_code == 409


def test_fix_limits(client, env, monkeypatch):
    jid = upload(client)
    monkeypatch.setattr(worker, "MAX_FIX_PER_JOB", 1)
    assert client.post(f"/jobs/{jid}/fix", data={"preset": "gfa_1250x560"}).status_code == 303
    assert client.post(f"/jobs/{jid}/fix", data={"preset": "gfa_1250x560"}).status_code == 409
    monkeypatch.setattr(worker, "MAX_FIX_PER_JOB", 9)
    monkeypatch.setattr(worker, "DAILY_TOKEN_LIMIT", 1)
    r = client.post(f"/jobs/{jid}/fix", data={"preset": "gfa_1250x560"})
    assert r.status_code == 409 and "상한" in r.json()["detail"]


def test_file_access_is_confined(client, env, tmp_path):
    jid = upload(client)
    res = store.load(jid)["results"]["gfa_1250x560"]
    assert client.get(f"/jobs/{jid}/files/{res['final']}").status_code == 200
    (tmp_path / "output" / "secret.json").write_text("{}")
    other = upload(client)
    assert client.get(f"/jobs/{jid}/files/secret.json").status_code == 404
    assert client.get(f"/jobs/{jid}/files/../secret.json").status_code == 404
    assert client.get(f"/jobs/{jid}/files/_jobs/{other}/job.json").status_code == 404
    assert client.get(f"/jobs/{jid}/files/%2e%2e/%2e%2e/pyproject.toml").status_code == 404
    assert client.get("/jobs/..%2f..%2fx").status_code == 404


def test_password_required(client, monkeypatch):
    monkeypatch.setenv("IMAGEIFX_WEB_PASSWORD", "pw")
    assert client.get("/").status_code == 401
    assert client.get("/", auth=("u", "pw")).status_code == 200
