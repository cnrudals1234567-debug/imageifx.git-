"""단일 워커 스레드: 분석 -> 변환 -> 수정을 한 번에 하나씩 처리한다(CPU 전용이라 동시 실행은 오히려 느림).

토큰은 '무료 단계 먼저'로 아낀다: 빠른 조정(quick)·자동 보정(auto)은 토큰 0, AI 수정(ai)은 가벼운 호출 1회."""
import json, os, queue, shutil, threading, traceback

from PIL import Image

from imageifx import cli
from . import agent, autotune, fixer, ops, store

MAX_AI_FIX_PER_SIZE = int(os.environ.get("IMAGEIFX_FIX_LIMIT_PER_SIZE", "3"))
DAILY_TOKEN_LIMIT = int(os.environ.get("IMAGEIFX_DAILY_TOKEN_LIMIT", "0"))      # 0 = 제한 없음
ANALYZE_TOKEN_CAP = int(os.environ.get("IMAGEIFX_ANALYZE_TOKEN_CAP", "30000"))   # 호출당 과금 토큰 상한
FIX_TOKEN_CAP = int(os.environ.get("IMAGEIFX_FIX_TOKEN_CAP", "40000"))

_q = queue.Queue()
_thread = None
claude = agent.run_claude     # 테스트에서 가짜로 바꾸는 지점
run_cli = ops.run_cli


def start():
    global _thread
    if _thread and _thread.is_alive():
        return
    _thread = threading.Thread(target=_loop, daemon=True, name="imageifx-worker")
    _thread.start()


def enqueue(task):
    _q.put(task)


def _loop():
    while True:
        t = _q.get()
        try:
            {"analyze": do_analyze, "convert": do_convert, "fix": do_fix}[t["kind"]](t)
        except Exception as e:   # 워커가 죽으면 이후 작업이 모두 멈추므로 작업 단위로 가둔다
            traceback.print_exc()
            _fail(t["job"], f"{type(e).__name__}: {e}", fix_n=t.get("n"))


def _settle(j):
    """대기·실행 중인 수정이 없으면 done, 있으면 fixing."""
    if j["status"] in ("done", "fixing") or j["status"] == "running":
        j["status"] = "fixing" if store.pending_fix(j) else "done"


def _fail(jid, msg, fix_n=None):
    def f(j):
        if fix_n:
            for fx in j["fixes"]:
                if fx["n"] == fix_n and fx["status"] in ("queued", "running"):
                    fx["status"], fx["error"] = "failed", msg
            j["status"] = "fixing" if store.pending_fix(j) else ("done" if j["results"] else "failed")
        else:
            j["status"], j["error"] = "failed", msg
    store.update(jid, f)
    store.log(jid, f"실패: {msg}")


def _stage(jid, status, stage):
    def f(j):
        j["status"], j["stage"] = status, stage
    store.update(jid, f)
    store.log(jid, stage)


def agent_dir(jid):
    d = os.path.join(store.job_dir(jid), "agent")
    os.makedirs(d, exist_ok=True)
    return d


def _call(jid, kind, system_prompt, user_prompt, cap, workdir, tools=("Read",)):
    """가벼운 claude 호출 + 사용량 기록. 토큰 상한을 넘기면 중단되고 사용량만 기록된다."""
    r = claude(user_prompt, cwd=workdir, system_prompt=system_prompt, tools=list(tools), lean=True,
               model=agent.DEFAULT_MODEL, effort=agent.DEFAULT_EFFORT, max_tokens=cap,
               on_progress=lambda m: store.log(jid, "  " + m))
    store.add_usage(jid, kind, "web-" + kind, r["usage"], r["cost"], r["ms"], r["turns"])
    if r.get("aborted"):
        raise agent.AgentError(f"토큰 상한({cap:,})을 넘어 중단했습니다. 사용량은 기록했습니다.")
    return r


# ---- 분석 ----
def do_analyze(t):
    jid = t["job"]
    job = store.load(jid)
    src = job["source"]
    size = Image.open(src).size
    out = os.path.join(store.job_dir(jid), "analysis.json")
    sha = store.image_sha(src) + "_" + fixer.ANALYZER_VERSION
    data = None if t.get("force") else store.cache_get(sha)
    if data and not fixer.validate_analysis(data, size):
        store.log(jid, "같은 이미지의 분석 캐시를 사용합니다(토큰 0).")
        _stage(jid, "analyzing", "분석 캐시 사용")
    else:
        data = None
        wd = agent_dir(jid)
        factor, small = fixer.make_small(src, os.path.join(wd, "source.png"))
        errors = None
        for attempt in (1, 2):
            _stage(jid, "analyzing", f"이미지 분석 중({attempt}/2)")
            msg = fixer.analyzer_message(small)
            if t.get("note"):
                msg += chr(10) + "사용자 지시: " + t["note"][:500]
            if errors:
                msg += "\n이전 답변의 오류를 고쳐서 다시 답하라:\n- " + "\n- ".join(errors)
            r = _call(jid, "analyze", fixer.analyzer_system_prompt(), msg, ANALYZE_TOKEN_CAP, wd)
            cand = agent.extract_json_block(r["text"])
            if cand is None:
                errors = ["JSON 블록이 없음"]
                continue
            cand = fixer.scale_analysis(cand, factor, size)
            errors = fixer.validate_analysis(cand, size)
            if not errors:
                data = cand
                store.cache_put(sha, data)
                break
        if not data:
            return _fail(jid, "분석 json 검증 실패: " + "; ".join((errors or ["알 수 없음"])[:5]))
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    fixer.draw_preview(src, data, os.path.join(store.job_dir(jid), "analysis_preview.png"))
    store.update(jid, lambda j: j.update(style=data["style"]))
    if job.get("confirm"):
        _stage(jid, "awaiting_confirm", "분석 완료: 확인 후 '실행'을 눌러 주세요")
    else:
        _stage(jid, "queued", "분석 완료")
        enqueue({"kind": "convert", "job": jid})


# ---- 변환 ----
def do_convert(t):
    """t["presets"]가 있으면 그 사이즈만(사이즈 추가), 없으면 작업에 지정한 전체. 첫 변환만 LaMa, 나머지는 분해 캐시 재사용."""
    jid = t["job"]
    job = store.load(jid)
    analysis = os.path.join(store.job_dir(jid), "analysis.json")
    targets = t.get("presets") or job["presets"]
    base = job.get("base_workdir") and store.ab(job["base_workdir"])
    for preset in targets:
        first = base is None
        _stage(jid, "running", f"{preset} 변환 중" + ("(분해·LaMa는 수 분 걸림)" if first else "(분해 캐시 재사용)"))
        if first:
            code, out = run_cli(["run", job["source"], analysis, "--preset", preset])
            wd = os.path.join(store.OUT, f"{jid}_{preset}")
        else:
            code, out = run_cli(["relayout", base, "--tag", preset, "--preset", preset])
            wd = os.path.join(store.OUT, f"{ops.read_layout(base)['name']}_{preset}")
        store.log(jid, "  " + "\n  ".join(out.strip().splitlines()[-3:]))
        if code not in (0, 1) or not os.path.exists(os.path.join(wd, "report.json")):
            res = {"error": f"변환 실패(코드 {code}): {out.strip()[-300:]}"}
        else:
            res = ops.summarize(wd)
            if first:
                base = wd
                store.update(jid, lambda j, b=store.rel(wd): j.update(base_workdir=b))
        store.update(jid, lambda j, p=preset, r=res: j["results"].__setitem__(p, r))
    if any("error" not in r for r in store.load(jid)["results"].values()):
        _stage(jid, "done", "변환 완료")
        store.update(jid, _settle)
        _auto_tune_new(jid, targets)
    else:
        _fail(jid, "모든 매체 변환이 실패했습니다")


def _auto_tune_new(jid, presets):
    """방금 만든 사이즈 중 배치 FAIL이 있는 것은 무료 자동 보정을 한 번 자동 실행한다(토큰 0, 수정 기록에 남아 되돌릴 수 있다).
    FAIL이 없는 결과(기준 GFA 포함)는 건드리지 않는다. 풀리지 않으면 기록 없이 원래 결과를 유지한다."""
    for preset in presets:
        j = store.load(jid)
        r = j["results"].get(preset)
        if not r or "error" in r or not r.get("fails"):
            continue
        cur = store.ab(r["workdir"])
        lay = ops.read_layout(cur)
        pr = cli._presets()[lay["preset"]]
        new, desc, b, a = autotune.solve(lay, pr, ops.load_layers(cur, lay))
        if new is None or a >= b:
            continue
        n = max([x["n"] for x in j["fixes"]] + [0]) + 1
        fx = {"n": n, "preset": preset, "kind": "auto", "label": "자동 보정(변환 직후 자동)", "status": "queued",
              "merge": False, "time": store.time.strftime("%Y-%m-%d %H:%M:%S")}
        store.update(jid, lambda x, f=fx: x["fixes"].append(f))
        try:
            do_fix({"job": jid, "preset": preset, "n": n, "fix_kind": "auto"})
        except Exception as e:   # 자동 보정은 덤이다. 실패해도 변환 결과는 그대로 둔다
            _fail(jid, f"{type(e).__name__}: {e}", fix_n=n)


# ---- 수정 ----
def _new_version_dir(jid, preset, n):
    return os.path.join(store.OUT, f"{jid}_{preset}_v{n}")


def _drop_relayout(jid, preset, cur_wd, new_wd, drop_ids, n):
    """장식을 빼려면 요소 목록이 바뀌므로 relayout이 필요하다. 임시 폴더에서 elements.json만 고쳐 새 폴더를 만든다."""
    stage = cur_wd + "_stg"
    ops.copy_version(cur_wd, stage)
    try:
        ep = os.path.join(stage, "elements.json")
        with open(ep, encoding="utf-8") as f:
            meta = json.load(f)
        for e in meta["elements"]:
            if e["id"] in drop_ids and e["importance"] in ("movable", "decor"):
                e["importance"] = "drop"
        with open(ep, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)
        code, out = run_cli(["relayout", stage, "--tag", f"d{n}", "--preset", preset])
        made = os.path.join(store.OUT, f"{ops.read_layout(stage)['name']}_d{n}")
        if code not in (0, 1) or not os.path.isdir(made):
            raise RuntimeError("장식 제거 재배치 실패: " + out.strip()[-200:])
        if os.path.exists(new_wd):
            shutil.rmtree(new_wd)
        shutil.move(made, new_wd)
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def do_fix(t):
    jid, preset, n, kind = t["job"], t["preset"], t["n"], t["fix_kind"]
    job = store.load(jid)
    fx0 = next(x for x in job["fixes"] if x["n"] == n)
    merge = bool(fx0.get("merge"))   # 빠른 조정을 이어서 누르면 같은 버전 폴더에서 계속 고친다(버전이 클릭마다 쌓이지 않게)
    before = fx0["before"] if merge else job["results"][preset]
    cur = store.ab(job["results"][preset]["workdir"])
    pr = cli._presets()[ops.read_layout(cur)["preset"]]

    def start(j):
        for fx in j["fixes"]:
            if fx["n"] == n:
                fx.update(status="running", before=before)
        j["status"], j["stage"] = "fixing", f"{preset} 수정 {n}회차({kind})"
    store.update(jid, start)
    store.log(jid, f"{preset} 수정 {n}회차 시작: {kind}")
    notes, extra = "", {}
    in_place = merge
    if merge:
        new_wd = cur
        backup = os.path.join(new_wd, "layout.json.bak")
        shutil.copy(os.path.join(new_wd, "layout.json"), backup)
    else:
        new_wd = _new_version_dir(jid, preset, n)
        ops.copy_version(cur, new_wd)
    try:
        if kind == "quick":
            drops = [o["id"] for o in t["ops"] if o.get("op") == "drop"]
            if drops:
                _drop_relayout(jid, preset, cur, new_wd, drops, n)
            todo = [o for o in t["ops"] if o.get("op") != "drop"]
            done, bad = ops.apply_ops(new_wd, todo)
            notes = f"빠른 조정: {len(done)}개 적용" + (f", 장식 {len(drops)}개 제거" if drops else "")
        elif kind == "auto":
            lay = ops.read_layout(new_wd)
            new, desc, b, a = autotune.solve(lay, pr, ops.load_layers(new_wd, lay))
            if new is None:
                raise RuntimeError(desc)
            ops.write_layout(new_wd, new)
            notes = f"자동 보정(토큰 0): {desc}. 배치 FAIL {b}→{a}"
        else:   # ai
            wd = agent_dir(jid)
            msg, _view = fixer.evidence(preset, pr, before, t.get("request", ""), wd, f"{preset}_{n}")
            r = _call(jid, "fix", fixer.prompt_text("fixer.md"), msg, FIX_TOKEN_CAP, wd)
            data = agent.extract_json_block(r["text"])
            lay = ops.read_layout(new_wd)
            with open(os.path.join(new_wd, "elements.json"), encoding="utf-8") as f:
                elements = json.load(f)["elements"]
            good, drops, bad, notes = fixer.parse_ai_ops(data, lay, elements)
            if drops:
                _drop_relayout(jid, preset, cur, new_wd, drops, n)
            done, bad2 = ops.apply_ops(new_wd, good)
            extra = {"ops": done, "rejected": [str(x) for x in bad] + [f"{o}: {why}" for o, why in bad2]}
            if not done and not drops:
                raise RuntimeError("AI가 적용할 연산을 주지 않았습니다. " + (notes or "요청을 구체적으로 써 보세요."))
        ok, out = ops.render(new_wd)
        if not ok:
            raise RuntimeError("렌더 실패: " + out.strip()[-200:])
    except Exception:
        if in_place:
            shutil.copy(os.path.join(new_wd, "layout.json.bak"), os.path.join(new_wd, "layout.json"))
        else:
            shutil.rmtree(new_wd, ignore_errors=True)
        raise
    if in_place:
        os.remove(os.path.join(new_wd, "layout.json.bak"))
    new = ops.summarize(new_wd)

    def f(j):
        j["results"][preset] = new
        for fx in j["fixes"]:
            if fx["n"] == n:
                fx.update(status="done", after=new, notes=notes, **extra)
        j["status"] = "fixing" if store.pending_fix(j) else "done"
    store.update(jid, f)
    store.log(jid, f"수정 {n}회차 완료: FAIL {before['fails']}→{new['fails']}")
