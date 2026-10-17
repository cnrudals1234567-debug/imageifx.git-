"""단일 워커 스레드: 분석 -> 변환 -> 수정 요청을 한 번에 하나씩 처리한다(CPU 전용이라 동시 실행은 오히려 느림)."""
import json, os, queue, re, subprocess, sys, threading, traceback

from PIL import Image

from . import agent, fixer, store

ANALYZER = "ad-image-analyzer"
FIXER = "ad-web-fixer"
ANALYZER_TOOLS = ["Read", "Write", "Bash(python -m uv run python:*)", "Bash(python -m uv run python *)"]
FIXER_TOOLS = ["Read", "Edit", *agent.ALLOWED_BASH]
MAX_FIX_PER_JOB = int(os.environ.get("IMAGEIFX_FIX_LIMIT_PER_JOB", "5"))
DAILY_TOKEN_LIMIT = int(os.environ.get("IMAGEIFX_DAILY_TOKEN_LIMIT", "0"))   # 0 = 제한 없음

_q = queue.Queue()
_thread = None
# 테스트에서 가짜로 바꾸는 지점
claude = agent.run_claude


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


def _fail(jid, msg, fix_n=None):
    def f(j):
        j["status"], j["error"] = "failed", msg
        if fix_n:
            for fx in j["fixes"]:
                if fx["n"] == fix_n:
                    fx["status"], fx["error"] = "failed", msg
            j["status"] = "done" if j["results"] else "failed"   # 수정이 실패해도 기존 결과는 그대로 유효
    store.update(jid, f)
    store.log(jid, f"실패: {msg}")


def _stage(jid, status, stage):
    def f(j):
        j["status"], j["stage"] = status, stage
    store.update(jid, f)
    store.log(jid, stage)


def _record(jid, kind, agent_name, r):
    store.add_usage(jid, kind, agent_name, r["usage"], r["cost"], r["ms"], r["turns"])


# ---- 분석 ----
def do_analyze(t):
    jid = t["job"]
    job = store.load(jid)
    src = job["source"]
    size = Image.open(src).size
    out = os.path.join(store.job_dir(jid), "analysis.json")
    errors = None
    for attempt in (1, 2):
        _stage(jid, "analyzing", f"이미지 분석 중({attempt}/2)")
        if os.path.exists(out):
            os.remove(out)
        r = claude(fixer.analyzer_prompt(src, out, size, errors), ANALYZER, ANALYZER_TOOLS,
                   on_progress=lambda m: store.log(jid, "  " + m))
        _record(jid, "analyze", ANALYZER, r)
        try:
            with open(out, encoding="utf-8") as f:
                data = json.load(f)
            errors = fixer.validate_analysis(data, size)
        except (OSError, ValueError) as e:
            errors = [f"분석 json을 읽을 수 없음: {e}"]
        if not errors:
            break
    if errors:
        return _fail(jid, "분석 json 검증 실패: " + "; ".join(errors[:5]))
    fixer.draw_preview(src, data, os.path.join(store.job_dir(jid), "analysis_preview.png"))
    store.update(jid, lambda j: j.update(style=data["style"]))
    if job.get("confirm"):
        _stage(jid, "awaiting_confirm", "분석 완료: 확인 후 '실행'을 눌러 주세요")
    else:
        _stage(jid, "queued", "분석 완료")
        enqueue({"kind": "convert", "job": jid})


# ---- 변환 ----
def run_cli(args):
    p = subprocess.run([sys.executable, "-m", "imageifx", *args], cwd=store.ROOT, capture_output=True, text=True,
                       encoding="utf-8", env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def find_final(workdir):
    """작업 폴더의 최종 이미지: layout.json의 name으로 만든 png/jpg 중 가장 최근 것(용량 초과 시 jpg가 나중에 써짐)."""
    with open(os.path.join(workdir, "layout.json"), encoding="utf-8") as f:
        name = json.load(f)["name"]
    c = [os.path.join(workdir, name + e) for e in (".png", ".jpg") if os.path.exists(os.path.join(workdir, name + e))]
    return max(c, key=os.path.getmtime) if c else None


def summarize(workdir):
    final = find_final(workdir)
    rep = os.path.join(workdir, "report.json")
    res = {"workdir": store.rel(workdir), "final": store.rel(final) if final else "", "report": "", "fails": 0, "warns": 0,
           "size_kb": round(os.path.getsize(final) / 1000) if final else 0}
    if os.path.exists(rep):
        with open(rep, encoding="utf-8") as f:
            rows = json.load(f)
        res.update(report=store.rel(rep), fails=sum(1 for r in rows if not r["ok"]),
                   warns=sum(1 for r in rows if r["ok"] and r.get("warn")))
    return res


def do_convert(t):
    jid = t["job"]
    job = store.load(jid)
    analysis = os.path.join(store.job_dir(jid), "analysis.json")
    base = None
    for i, preset in enumerate(job["presets"]):
        _stage(jid, "running", f"{preset} 변환 중" + ("(분해·LaMa는 수 분 걸림)" if i == 0 else "(분해 캐시 재사용)"))
        if i == 0:
            code, out = run_cli(["run", job["source"], analysis, "--preset", preset])
            wd = os.path.join(store.OUT, f"{jid}_{preset}")
        else:
            if not base:
                res = {"error": "첫 매체 변환 실패로 분해 캐시가 없음"}
                store.update(jid, lambda j, p=preset, r=res: j["results"].__setitem__(p, r))
                continue
            code, out = run_cli(["relayout", base, "--tag", preset, "--preset", preset])
            with open(os.path.join(base, "layout.json"), encoding="utf-8") as f:
                wd = os.path.join(store.OUT, f"{json.load(f)['name']}_{preset}")
        store.log(jid, "  " + "\n  ".join(out.strip().splitlines()[-4:]))
        if code not in (0, 1) or not os.path.exists(os.path.join(wd, "report.json")):
            res = {"error": f"변환 실패(코드 {code}): {out.strip()[-300:]}"}
        else:
            res = summarize(wd)
            if i == 0:
                base = wd
        store.update(jid, lambda j, p=preset, r=res: j["results"].__setitem__(p, r))
    ok = any("error" not in r for r in store.load(jid)["results"].values())
    if ok:
        _stage(jid, "done", "변환 완료")
    else:
        _fail(jid, "모든 매체 변환이 실패했습니다")


# ---- 수정 요청 ----
def do_fix(t):
    jid, preset, n = t["job"], t["preset"], t["n"]
    job = store.load(jid)
    res = job["results"][preset]
    _stage(jid, "fixing", f"{preset} 수정 {n}회차: 에이전트가 오류를 확인하고 고치는 중")
    snap = fixer.snapshot(jid, preset, n, res)
    store.update(jid, lambda j: [fx.update(before_snap=snap, before=res) for fx in j["fixes"] if fx["n"] == n])
    guard_before = fixer.protected_state()
    r = claude(fixer.fixer_prompt(job, preset, res, t.get("request", ""), n), FIXER, FIXER_TOOLS,
               on_progress=lambda m: store.log(jid, "  " + m))
    _record(jid, "fix", FIXER, r)
    report = agent.extract_json_block(r["text"]) or {}
    wd_name = str(report.get("final_workdir") or "")
    wd = os.path.join(store.OUT, wd_name) if wd_name and os.sep not in wd_name and "/" not in wd_name and wd_name.startswith(jid) else None
    if not (wd and os.path.isdir(wd)):
        wd = store.ab(res["workdir"])
    new = summarize(wd)
    warn = ""
    if guard_before is not None and fixer.protected_state() != guard_before:
        warn = "에이전트가 보호 경로(imageifx/, presets/ 등)를 수정했습니다. git diff 로 확인하세요."

    def f(j):
        j["results"][preset] = new
        for fx in j["fixes"]:
            if fx["n"] == n:
                fx.update(status="done", after=new, report=report, text=r["text"][-1500:], warning=warn, error_flag=r["is_error"])
        j["status"] = "done"
    store.update(jid, f)
    store.log(jid, f"수정 {n}회차 완료: FAIL {res['fails']}→{new['fails']}")
