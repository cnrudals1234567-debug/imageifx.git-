import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


@pytest.fixture(scope="session")
def presets():
    with open(os.path.join(ROOT, "presets", "presets.json"), encoding="utf-8") as f:
        return {k: v for k, v in json.load(f).items() if not k.startswith("_")}


@pytest.fixture(scope="session")
def gfa(presets):
    return presets["gfa_1250x560"]


def make_layer(lid, x, y, w, h, scale=1.0, typ="text", importance="critical", **extra):
    """테스트용 layout 레이어 dict. src_bbox 크기는 w x h."""
    d = {"id": lid, "type": typ, "importance": importance, "src_bbox": [0, 0, w, h], "scale": scale, "x": x, "y": y}
    d.update(extra)
    return d
