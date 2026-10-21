"""场景配置加载（L1 进程内缓存的载体）：yaml → dataclass，进程级单例 + 热加载。"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

import yaml

_lock = threading.Lock()
_cache: dict[str, "ScenarioConfig"] = {}


@dataclass
class ScenarioConfig:
    name: str
    routes: list[str]
    thresholds: dict
    greeting_patterns: list[str]
    alarm_code_patterns: list[str]
    injection_patterns: list[str]
    out_of_scope_patterns: list[str]
    keyword_rules: dict[str, dict[str, float]]
    hybrid_weights: dict
    filters_by_intent: dict
    abbreviations: dict[str, str]
    limits: dict
    timeouts_ms: dict
    _compiled: dict = field(default_factory=dict, repr=False)

    # ---- 预编译正则（编译一次，L1 缓存语义） ----

    def _regex(self, key: str, patterns: list[str]) -> list:
        if key not in self._compiled:
            import re

            self._compiled[key] = [re.compile(p, re.IGNORECASE) for p in patterns]
        return self._compiled[key]

    @property
    def greeting_res(self):
        return self._regex("greeting", self.greeting_patterns)

    @property
    def alarm_res(self):
        return self._regex("alarm", self.alarm_code_patterns)

    @property
    def injection_res(self):
        return self._regex("injection", self.injection_patterns)

    @property
    def oos_res(self):
        return self._regex("oos", self.out_of_scope_patterns)

    @classmethod
    def from_dict(cls, raw: dict) -> "ScenarioConfig":
        return cls(
            name=raw["name"],
            routes=raw["routes"],
            thresholds=raw["thresholds"],
            greeting_patterns=raw["greeting_patterns"],
            alarm_code_patterns=raw["alarm_code_patterns"],
            injection_patterns=raw["injection_patterns"],
            out_of_scope_patterns=raw["out_of_scope_patterns"],
            keyword_rules=raw["keyword_rules"],
            hybrid_weights=raw["hybrid_weights"],
            filters_by_intent=raw["filters_by_intent"],
            abbreviations=raw["abbreviations"],
            limits=raw["limits"],
            timeouts_ms=raw["timeouts_ms"],
        )

    def hybrid_weights_for(self, doc_types: list[str] | None) -> tuple[float, float]:
        if doc_types and "fault_case" in doc_types:
            w = self.hybrid_weights["fault_case"]
        else:
            w = self.hybrid_weights["default"]
        return float(w["dense"]), float(w["sparse"])

    def doc_type_priority(self, intent: str) -> list[str] | None:
        cfg = self.filters_by_intent.get(intent)
        return cfg["doc_type_priority"] if cfg else None


def load_scenario(path: str | Path) -> ScenarioConfig:
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return ScenarioConfig.from_dict(raw)


def _default_scenarios_dir() -> Path:
    """定位 scenarios 目录：优先环境变量，其次按包位置推断。

    项目里 scenarios 存在两份：worktree 下的一份（随代码入库）与运行数据目录下的一份
    （compose 通过 APP_SCENARIOS_DIR 挂到容器 /app/scenarios）。
    若只按 `__file__` 相对定位，容器内（/app/scenarios）与宿主机跑脚本
    （<worktree>/scenarios）会读到不同文件，出现"改了一份、另一份生效"的诡异现象，
    因此这里统一以环境变量为准。
    """
    for key in ("SCENARIOS_DIR", "APP_SCENARIOS_DIR"):
        value = os.getenv(key)
        if value:
            return Path(value)
    return Path(__file__).resolve().parents[2] / "scenarios"


def get_scenario(name: str = "telecom_ops", base_dir: str | Path | None = None,
                 reload: bool = False) -> ScenarioConfig:
    """进程内单例（L1 缓存）。reload=True 供配置热加载接口调用。"""
    with _lock:
        if name not in _cache or reload:
            base = Path(base_dir) if base_dir else _default_scenarios_dir()
            _cache[name] = load_scenario(base / f"{name}.yaml")
        return _cache[name]
