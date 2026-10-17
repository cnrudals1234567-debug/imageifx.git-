"""디자인 스펙(presets/design_spec.json)과 스타일 프로필(presets/styles/*.json) 로더.

디자인에 영향을 주는 숫자는 코드에 직접 쓰지 않고 여기서 읽는다. 그래야 값이 한 곳에 모이고, 바뀔 때 눈에 띈다.
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_cache = {}


def _read(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def learned():
    """웹의 피드백 학습이 만든 덮어쓰기({"design": {점경로: 값}, "style": {키: 값}}). 환경변수 IMAGEIFX_LEARNED가 있을 때만 쓴다.
    CLI를 직접 실행하면 없으므로 기준 디자인은 그대로 재현된다(규칙 4)."""
    p = os.environ.get("IMAGEIFX_LEARNED")
    if not p or not os.path.exists(p):
        return {}
    key = ("learned", p, os.path.getmtime(p))
    if key not in _cache:
        _cache[key] = _read(p)
    return _cache[key]


def _set_dotted(d, dotted, value):
    node = d
    parts = dotted.split(".")
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def spec():
    ov = learned().get("design")
    if ov:
        key = ("spec+", os.environ.get("IMAGEIFX_LEARNED"), os.path.getmtime(os.environ["IMAGEIFX_LEARNED"]))
        if key not in _cache:
            import copy
            d = copy.deepcopy(spec_base())
            for k, v in ov.items():
                _set_dotted(d, k, v)
            _cache[key] = d
        return _cache[key]
    return spec_base()


def spec_base():
    if "spec" not in _cache:
        _cache["spec"] = _read(os.path.join(ROOT, "presets", "design_spec.json"))
    return _cache["spec"]


def get(dotted, default=None):
    """spec 값을 점 경로로 읽는다. 예: get("scrim.alpha"). 없는 경로는 default 대신 KeyError(오타를 조용히 넘기지 않음)."""
    node = spec()
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            if default is not None:
                return default
            raise KeyError(f"design_spec.json에 '{dotted}'가 없습니다")
        node = node[part]
    return node


def style_names():
    d = os.path.join(ROOT, "presets", "styles")
    return sorted(os.path.splitext(f)[0] for f in os.listdir(d) if f.endswith(".json"))


def style(name):
    p = os.path.join(ROOT, "presets", "styles", f"{name}.json")
    if not os.path.exists(p):
        raise KeyError(f"스타일 '{name}'이 없습니다. 사용 가능: {', '.join(style_names())}")
    d = dict(_read(p))
    d.update(learned().get("style", {}))   # 학습된 스타일 값(웹 전용). 기준 재현에는 영향 없음
    return d


def resolve_options(style_dict, preset, src_size, overrides=None):
    """스타일 프로필 + (선택) 덮어쓰기 -> 배치 옵션. 스타일의 비율 값을 캔버스/원본 크기에 맞춰 정수 px로 바꾼다."""
    W, H = preset["size"]
    _, sh = src_size
    o = {
        "text_side": style_dict.get("text_side", "right"),
        "scrim": bool(style_dict.get("scrim", False)),
        "text_scale": style_dict.get("text_scale", 1.0),
        "person_h": int(round(style_dict["person_h_ratio"] * H)) if "person_h_ratio" in style_dict else None,
        "bg_top": int(round(style_dict["bg_top_ratio"] * sh)) if "bg_top_ratio" in style_dict else None,
        "text_width_ratio": style_dict.get("text_width_ratio"),
        "text_v_ratio": style_dict.get("text_v_ratio"),
    }
    for k, v in (overrides or {}).items():
        if v is not None:
            o[k] = v
    return o
