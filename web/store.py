"""작업(job) 저장소: output/_jobs/<id>/job.json 파일 하나가 작업 상태의 전부다. DB를 쓰지 않는다."""
import hashlib, json, os, threading, time, uuid
from datetime import datetime

from imageifx import spec

ROOT = spec.ROOT
OUT = os.path.join(ROOT, "output")
JOBS = os.path.join(OUT, "_jobs")
INPUT = os.path.join(ROOT, "input")
USAGE = os.path.join(OUT, "_usage.jsonl")
CACHE = os.path.join(OUT, "_analysis_cache")
_lock = threading.RLock()

ACTIVE = ("queued", "analyzing", "running", "fixing")


def job_dir(jid):
    return os.path.join(JOBS, jid)


def new_id():
    return datetime.now().strftime("j%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:4]


def create(jid, **fields):
    os.makedirs(job_dir(jid), exist_ok=True)
    job = {"id": jid, "created": time.strftime("%Y-%m-%d %H:%M:%S"), "status": "queued", "stage": "", "error": "",
           "results": {}, "fixes": [], "feedback": [], "likes": [], "usage": [], "log": []}
    job.update(fields)
    save(job)
    return job


def load(jid):
    p = os.path.join(job_dir(jid), "job.json")
    if not os.path.exists(p):
        return None
    with _lock, open(p, encoding="utf-8") as f:
        j = json.load(f)
    j.setdefault("feedback", [])   # 피드백 기능 이전에 만든 작업과의 호환
    j.setdefault("likes", [])
    return j


def save(job):
    p = os.path.join(job_dir(job["id"]), "job.json")
    with _lock:
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(job, f, ensure_ascii=False, indent=1)
        os.replace(tmp, p)


def update(jid, fn):
    """읽고-고치고-저장을 한 번에(워커와 요청 스레드가 같은 파일을 동시에 고치지 않도록)."""
    with _lock:
        job = load(jid)
        fn(job)
        save(job)
        return job


def log(jid, msg):
    def f(j):
        j["log"].append(f"{time.strftime('%H:%M:%S')} {msg}")
        j["log"] = j["log"][-200:]
    update(jid, f)


def list_jobs():
    if not os.path.isdir(JOBS):
        return []
    jobs = [load(d) for d in os.listdir(JOBS) if os.path.isdir(job_dir(d))]
    return sorted([j for j in jobs if j], key=lambda j: j["created"], reverse=True)


def rel(path):
    """output/ 기준 상대경로(URL 용, 슬래시)."""
    return os.path.relpath(path, OUT).replace("\\", "/")


def ab(relpath):
    return os.path.join(OUT, relpath)


# ---- 토큰 사용량 ----
def total_tokens(u):
    """과금 대상 토큰: 입력 + 출력 + 캐시 생성. 캐시 읽기는 따로 보여 준다."""
    return u.get("input", 0) + u.get("output", 0) + u.get("cache_creation", 0)


def add_usage(jid, kind, agent, usage, cost, ms, turns):
    entry = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "job": jid, "kind": kind, "agent": agent,
             "input": usage.get("input", 0), "output": usage.get("output", 0),
             "cache_read": usage.get("cache_read", 0), "cache_creation": usage.get("cache_creation", 0),
             "cost_usd": round(cost or 0, 4), "ms": ms, "turns": turns}
    with _lock:
        os.makedirs(OUT, exist_ok=True)
        with open(USAGE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    update(jid, lambda j: j["usage"].append(entry))
    return entry


def all_usage():
    if not os.path.exists(USAGE):
        return []
    with _lock, open(USAGE, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def sum_usage(entries):
    s = {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0, "cost_usd": 0.0, "runs": len(entries)}
    for e in entries:
        for k in ("input", "output", "cache_read", "cache_creation", "cost_usd"):
            s[k] += e.get(k, 0)
    s["total"] = total_tokens(s)
    return s


def usage_today():
    day = time.strftime("%Y-%m-%d")
    return sum_usage([e for e in all_usage() if e["time"].startswith(day)])


def usage_month():
    mon = time.strftime("%Y-%m")
    return sum_usage([e for e in all_usage() if e["time"].startswith(mon)])


# ---- 분석 캐시: 같은 이미지를 다시 올리면 분석 토큰을 쓰지 않는다 ----
def image_sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:24]


def cache_get(sha):
    p = os.path.join(CACHE, sha + ".json")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def cache_put(sha, data):
    os.makedirs(CACHE, exist_ok=True)
    with open(os.path.join(CACHE, sha + ".json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def pending_fix(job, preset=None):
    return [fx for fx in job["fixes"] if fx["status"] in ("queued", "running") and (preset is None or fx["preset"] == preset)]


def fix_counts(jobs):
    """수정 종류별 횟수(무료=auto·quick, AI=ai)."""
    c = {"auto": 0, "quick": 0, "ai": 0}
    for j in jobs:
        for fx in j["fixes"]:
            if fx.get("status") == "done":
                c[fx.get("kind", "ai")] = c.get(fx.get("kind", "ai"), 0) + 1
    return c
