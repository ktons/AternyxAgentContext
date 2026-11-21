#!/usr/bin/env python3
"""闭环参数搜索：CMA-ES / TPE，复合分做目标，带缓存与 trial log。

定位：图形迭代「参数搜索」母本（对应 references/08-search.md）。它只负责「提议参数 →
调用评估器 → 读回分数 → 更新代理」，引擎交互放在项目适配器（模板见
`templates/lookdev_trial_template.py`：应用参数 → 出图 → 评分 → 写 `trial_score.json`）。

空间约定（search spec JSON）：
  {
    "space": {
      "exposure":  {"kind": "float", "min": 0.7, "max": 1.5, "default": 1.0},
      "ambient":   {"kind": "float", "min": 0.05, "max": 0.4, "log": true},
      "gradeLut":  {"kind": "choice", "choices": ["warm", "neutral"]},
      "keyAzimuth":{"kind": "float", "min": -70, "max": 70, "step": 5}
    },
    "budget": {"trials": 40, "seed": 7, "optimizer": "cmaes", "max_seconds": 3600},
    "evaluator": {"command": "python <adapter>.py --params-file {params} --out {out}",
                   "timeout": 1200, "workdir": "."},
    "objective": {"tokens": "...", "profile": "day", "required_legs": ["<leg-1>"]},
    "cache_dir": "temp/<任务>/search_cache"
  }

评估器契约（最小面）：
  - 读 `{params}` 指向的 JSON（参数名 → 值）；
  - 把结果写到 `{out}/trial_score.json`，至少含 `{"score": number, "feasible": bool}`，
    建议附 `components`/`per_leg`；失败时写 `{"score": null, "feasible": false, "error": "..."}`
    或以非零退出（框架按失败计入并继续）。

纪律：
  - 只优化 token 定义的复合分（见 score_trial.py），把「看起来」交给人工判读；
  - 每次 trial 记录参数/分数/缓存命中/耗时，搜索结束用**留出 leg + 全量 leg 集**复验
    最优解，再进入验收（见 references/08-search.md）；
  - 同一 spec 重跑命中缓存；换 token/窗口/leg 集时新建搜索，从新分数起算。

用法：
  python .../param_search.py --spec temp/<任务>/search.json --out temp/<任务>/search
  python .../param_search.py --self-test
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import subprocess
import sys
import time
from pathlib import Path

SEARCH_VERSION = 1
FAIL_SCORE = 1.0e6


# ======================================================================================
# 空间编码
# ======================================================================================

class Space:
    def __init__(self, spec: dict):
        if not isinstance(spec, dict) or not spec:
            raise SystemExit("search.space 不能为空")
        self.params: list[dict] = []
        for name, definition in spec.items():
            kind = definition.get("kind", "float")
            if kind not in ("float", "int", "choice"):
                raise SystemExit(f"参数 {name}: 未知 kind {kind}")
            entry = {"name": name, "kind": kind, **definition}
            if kind in ("float", "int"):
                if "min" not in entry or "max" not in entry:
                    raise SystemExit(f"参数 {name}: 需要 min/max")
                if entry["max"] <= entry["min"]:
                    raise SystemExit(f"参数 {name}: max 必须大于 min")
            else:
                choices = entry.get("choices")
                if not isinstance(choices, list) or not choices:
                    raise SystemExit(f"参数 {name}: choice 需要非空 choices")
            if "default" in entry and not self._default_valid(entry):
                raise SystemExit(f"参数 {name}: default 不在取值范围内")
            self.params.append(entry)
        self.dim = len(self.params)

    def _default_valid(self, entry: dict) -> bool:
        value = entry["default"]
        if entry["kind"] == "choice":
            return value in entry["choices"]
        try:
            number = float(value)
        except (TypeError, ValueError):
            return False
        return entry["min"] <= number <= entry["max"]

    def encode(self, params: dict) -> list[float]:
        vector = []
        for entry in self.params:
            value = params.get(entry["name"], entry.get("default"))
            if entry["kind"] == "choice":
                index = entry["choices"].index(value)
                vector.append(index / max(1, len(entry["choices"]) - 1))
            else:
                low, high = float(entry["min"]), float(entry["max"])
                if entry.get("log"):
                    low, high = math.log(low), math.log(high)
                    current = math.log(max(1e-12, float(value)))
                else:
                    current = float(value)
                vector.append((current - low) / max(1e-12, high - low))
        return [min(1.0, max(0.0, value)) for value in vector]

    def decode(self, vector: list[float]) -> dict:
        params = {}
        for entry, value in zip(self.params, vector):
            value = min(1.0, max(0.0, value))
            if entry["kind"] == "choice":
                index = min(len(entry["choices"]) - 1,
                            int(round(value * (len(entry["choices"]) - 1))))
                params[entry["name"]] = entry["choices"][index]
            elif entry["kind"] == "int":
                low, high = int(entry["min"]), int(entry["max"])
                number = int(round(low + value * (high - low)))
                step = int(entry.get("step", 1))
                if step > 1:
                    number = low + int(round((number - low) / step)) * step
                params[entry["name"]] = min(high, max(low, number))
            else:
                low, high = float(entry["min"]), float(entry["max"])
                if entry.get("log"):
                    number = math.exp(math.log(low) + value * (math.log(high) - math.log(low)))
                else:
                    number = low + value * (high - low)
                step = entry.get("step")
                if step:
                    number = round(number / float(step)) * float(step)
                params[entry["name"]] = round(number, 6)
        return params

    def default_vector(self) -> list[float]:
        defaults = {entry["name"]: entry["default"]
                    for entry in self.params if "default" in entry}
        if len(defaults) == self.dim:
            return self.encode(defaults)
        return [0.5] * self.dim

    def matrix(self) -> dict:
        return {"params": [dict(entry) for entry in self.params]}


# ======================================================================================
# CMA-ES（有界 [0,1]^n，可分离全协方差；n 小，纯 Python 足够）
# ======================================================================================

class CMAES:
    def __init__(self, dim: int, mean: list[float], sigma: float, seed: int):
        self.dim = dim
        self.mean = list(mean)
        self.sigma = sigma
        self.rng = random.Random(seed)
        self.lam = 4 + int(3.0 * math.log(dim))
        self.mu = self.lam // 2
        weights = [math.log(self.mu + 0.5) - math.log(i + 1) for i in range(self.mu)]
        total = sum(weights)
        self.weights = [weight / total for weight in weights]
        self.mueff = 1.0 / sum(weight ** 2 for weight in self.weights)
        self.cc = (4.0 + self.mueff / dim) / (dim + 4.0 + 2.0 * self.mueff / dim)
        self.cs = (self.mueff + 2.0) / (dim + self.mueff + 5.0)
        self.c1 = 2.0 / ((dim + 1.3) ** 2 + self.mueff)
        self.cmu = min(1.0 - self.c1,
                       2.0 * (self.mueff - 2.0 + 1.0 / self.mueff) / ((dim + 2.0) ** 2 + self.mueff))
        self.damps = 1.0 + 2.0 * max(0.0, math.sqrt((self.mueff - 1.0) / (dim + 1.0)) - 1.0) + self.cs
        self.chi_n = math.sqrt(dim) * (1.0 - 1.0 / (4.0 * dim) + 1.0 / (21.0 * dim * dim))
        self.pc = [0.0] * dim
        self.ps = [0.0] * dim
        self.cov = [[1.0 if i == j else 0.0 for j in range(dim)] for i in range(dim)]
        self.generation = 0
        self._chol = self._cholesky(self.cov)

    @staticmethod
    def _cholesky(matrix: list[list[float]]) -> list[list[float]]:
        n = len(matrix)
        low = [[0.0] * n for _ in range(n)]
        for i in range(n):
            for j in range(i + 1):
                total = matrix[i][j] - sum(low[i][k] * low[j][k] for k in range(j))
                if i == j:
                    low[i][j] = math.sqrt(max(1e-18, total))
                else:
                    low[i][j] = total / low[j][j]
        return low

    def _multiply_low(self, vector: list[float]) -> list[float]:
        out = []
        for i in range(self.dim):
            out.append(sum(self._chol[i][k] * vector[k] for k in range(i + 1)))
        return out

    def _solve_low(self, vector: list[float]) -> list[float]:
        out = [0.0] * self.dim
        for i in range(self.dim):
            total = vector[i] - sum(self._chol[i][k] * out[k] for k in range(i))
            out[i] = total / self._chol[i][i]
        return out

    def ask(self) -> list[list[float]]:
        population = []
        for _ in range(self.lam):
            z = [self.rng.gauss(0.0, 1.0) for _ in range(self.dim)]
            y = self._multiply_low(z)
            x = [min(1.0, max(0.0, self.mean[i] + self.sigma * y[i]))
                 for i in range(self.dim)]
            population.append(x)
        self._generation_population = population
        return population

    def tell(self, population: list[list[float]], scores: list[float]) -> None:
        order = sorted(range(len(population)), key=lambda index: scores[index])
        old_mean = list(self.mean)
        selected = [population[index] for index in order[:self.mu]]
        self.mean = [sum(self.weights[i] * selected[i][d] for i in range(self.mu))
                     for d in range(self.dim)]
        self.mean = [min(1.0, max(0.0, value)) for value in self.mean]
        step = [(self.mean[d] - old_mean[d]) / self.sigma for d in range(self.dim)]
        inv_step = self._solve_low(step)
        c_sigma = (self.cs * (2.0 - self.cs) * self.mueff) ** 0.5
        self.ps = [(1.0 - self.cs) * self.ps[d] + c_sigma * inv_step[d]
                   for d in range(self.dim)]
        norm_ps = math.sqrt(sum(value * value for value in self.ps))
        h_sigma = 1.0 if norm_ps / math.sqrt(1.0 - (1.0 - self.cs) ** (2.0 * (self.generation + 1))) \
            < (1.4 + 2.0 / (self.dim + 1.0)) * self.chi_n else 0.0
        c_c = (self.cc * (2.0 - self.cc) * self.mueff) ** 0.5
        self.pc = [(1.0 - self.cc) * self.pc[d]
                   + h_sigma * c_c * step[d] for d in range(self.dim)]
        rank_mu = [[0.0] * self.dim for _ in range(self.dim)]
        for index in range(self.mu):
            y = [(population[order[index]][d] - old_mean[d]) / self.sigma
                 for d in range(self.dim)]
            weight = self.weights[index]
            for i in range(self.dim):
                for j in range(self.dim):
                    rank_mu[i][j] += weight * y[i] * y[j]
        for i in range(self.dim):
            for j in range(self.dim):
                self.cov[i][j] = ((1.0 - self.c1 - self.cmu) * self.cov[i][j]
                                  + self.c1 * (self.pc[i] * self.pc[j]
                                               + (1.0 - h_sigma) * self.cc * (2.0 - self.cc)
                                               * self.cov[i][j])
                                  + self.cmu * rank_mu[i][j])
        self.sigma *= math.exp((self.cs / self.damps) * (norm_ps / self.chi_n - 1.0))
        self.sigma = min(0.5, max(1e-4, self.sigma))
        self.generation += 1
        self._chol = self._cholesky(self.cov)


# ======================================================================================
# TPE（独立维度 KDE；适合离散/混合空间与昂贵评估）
# ======================================================================================

class TPE:
    def __init__(self, dim: int, seed: int, gamma: float = 0.25, n_candidates: int = 24):
        self.dim = dim
        self.rng = random.Random(seed)
        self.gamma = gamma
        self.n_candidates = n_candidates
        self.history: list[tuple[list[float], float]] = []
        self.initial = max(8, 2 * dim)

    def _sample_kde(self, points: list[list[float]]) -> list[float]:
        point = self.rng.choice(points)
        sample = []
        for d in range(self.dim):
            spread = 0.15
            if len(points) > 1:
                values = [entry[d] for entry in points]
                mean = sum(values) / len(values)
                variance = sum((value - mean) ** 2 for value in values) / len(values)
                spread = max(0.03, math.sqrt(variance) * (len(points) ** -0.2))
            sample.append(min(1.0, max(0.0, point[d] + self.rng.gauss(0.0, spread))))
        return sample

    @staticmethod
    def _density(points: list[list[float]], vector: list[float], dim: int) -> float:
        if not points:
            return 1e-9
        total = 0.0
        for point in points:
            distance = 0.0
            for d in range(dim):
                spread = 0.15
                distance += ((vector[d] - point[d]) / spread) ** 2
            total += math.exp(-0.5 * distance)
        return total / len(points)

    def ask(self) -> list[float]:
        if len(self.history) < self.initial:
            return [self.rng.random() for _ in range(self.dim)]
        ordered = sorted(self.history, key=lambda item: item[1])
        good_count = max(2, int(len(ordered) * self.gamma))
        good = [item[0] for item in ordered[:good_count]]
        all_points = [item[0] for item in ordered]
        best_vector = None
        best_ratio = -1.0
        for _ in range(self.n_candidates):
            if self.rng.random() < 0.5:
                candidate = self._sample_kde(good)
            else:
                candidate = self._sample_kde(all_points)
            ratio = (self._density(good, candidate, self.dim)
                     / max(1e-9, self._density(all_points, candidate, self.dim)))
            if ratio > best_ratio:
                best_ratio = ratio
                best_vector = candidate
        return best_vector or [self.rng.random() for _ in range(self.dim)]

    def tell(self, vector: list[float], score: float) -> None:
        self.history.append((list(vector), score))


# ======================================================================================
# 评估器
# ======================================================================================

def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def params_hash(params: dict) -> str:
    return hashlib.sha256(canonical_json(params).encode("utf-8")).hexdigest()[:24]


def run_evaluator(command: str, params_path: Path, out_dir: Path, timeout: float,
                  workdir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    formatted = command.format(params=str(params_path), out=str(out_dir))
    started = time.time()
    try:
        completed = subprocess.run(formatted, shell=True, cwd=str(workdir),
                                   timeout=timeout, capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        return {"score": None, "feasible": False, "error": f"evaluator 超时 {timeout}s",
                "duration": round(time.time() - started, 3)}
    duration = round(time.time() - started, 3)
    score_path = out_dir / "trial_score.json"
    if not score_path.is_file():
        return {"score": None, "feasible": False,
                "error": f"评估器未写 {score_path}（rc={completed.returncode}）",
                "stdout": completed.stdout[-2000:], "stderr": completed.stderr[-2000:],
                "duration": duration}
    try:
        result = json.loads(score_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        return {"score": None, "feasible": False, "error": f"trial_score.json 非法: {exc}",
                "duration": duration}
    result.setdefault("duration", duration)
    result.setdefault("feasible", True)
    return result


def build_params(values: dict, spec: dict) -> dict:
    """把扁平搜索参数按 spec.mapping 组装成评估器参数（适配器 schema）。"""
    mapping = spec.get("mapping") or {}
    params = json.loads(json.dumps(spec.get("fixed") or {}))
    for name, value in values.items():
        target = mapping.get(name)
        if not isinstance(target, dict):
            raise SystemExit(f"spec.mapping 缺参数 '{name}' 的目标")
        kind = target.get("target")
        if kind == "volume":
            params.setdefault("volume", {})[target["field"]] = value
        elif kind == "light":
            params.setdefault("lights", {}).setdefault(target["entity"], {})[
                target["component"]] = value
        elif kind == "material":
            entries = params.setdefault("materials", [])
            entry = next((item for item in entries
                          if item.get("path") == target["path"]
                          and item.get("property") == target["property"]), None)
            if entry is None:
                entry = {"path": target["path"], "property": target["property"], "set": {}}
                entries.append(entry)
            entry.setdefault("set", {})[str(int(target["index"]))] = value
        else:
            raise SystemExit(f"spec.mapping['{name}'].target 未知: {kind}")
    return params


def validate_spec(spec: dict) -> None:
    space = Space(spec.get("space") or {})
    mapping = spec.get("mapping") or {}
    missing = [entry["name"] for entry in space.params if entry["name"] not in mapping]
    if missing:
        raise SystemExit(f"spec.mapping 缺映射: {missing}")
    for name, target in mapping.items():
        if not isinstance(target, dict) or target.get("target") not in (
                "volume", "light", "material"):
            raise SystemExit(f"spec.mapping['{name}'] 非法: {target}")
        if target.get("target") == "light" and not {"entity", "component"} <= set(target):
            raise SystemExit(f"spec.mapping['{name}']: light 需要 entity/component")
        if target.get("target") == "material" and not {"path", "property", "index"} <= set(target):
            raise SystemExit(f"spec.mapping['{name}']: material 需要 path/property/index")


def optimize(spec: dict, out_dir: Path) -> dict:
    space = Space(spec["space"])
    validate_spec(spec)
    budget = spec.get("budget") or {}
    trials = int(budget.get("trials", 40))
    seed = int(budget.get("seed", 7))
    optimizer = budget.get("optimizer", "cmaes")
    max_seconds = float(budget.get("max_seconds", 0) or 0)
    evaluator = spec.get("evaluator") or {}
    command = evaluator.get("command")
    timeout = float(evaluator.get("timeout", 1200))
    workdir = Path(evaluator.get("workdir", "."))
    cache_dir = Path(spec.get("cache_dir") or (out_dir / "cache"))
    cache_dir.mkdir(parents=True, exist_ok=True)
    trials_path = out_dir / "trials.jsonl"

    if optimizer == "cmaes":
        searcher = CMAES(space.dim, space.default_vector(), sigma=float(budget.get("sigma", 0.25)),
                         seed=seed)
    elif optimizer == "tpe":
        searcher = TPE(space.dim, seed)
    else:
        raise SystemExit(f"未知 optimizer '{optimizer}'（cmaes|tpe）")

    history: list[dict] = []
    best: dict | None = None
    started = time.time()
    while len(history) < trials:
        if max_seconds and time.time() - started > max_seconds:
            print(f"[param_search] 达到 max_seconds={max_seconds}s，提前停止（{len(history)} trials）")
            break
        if optimizer == "cmaes":
            population = searcher.ask()
            batch = [(vector, build_params(space.decode(vector), spec)) for vector in population]
        else:
            vector = searcher.ask()
            batch = [(vector, build_params(space.decode(vector), spec))]

        scores = []
        for vector, params in batch:
            if len(history) >= trials:
                break
            trial_index = len(history)
            key = params_hash(params)
            cache_path = cache_dir / f"{key}.json"
            cached = cache_path.is_file()
            if cached:
                result = json.loads(cache_path.read_text(encoding="utf-8-sig"))
            else:
                params_path = out_dir / f"trial_{trial_index:04d}_params.json"
                params_path.write_text(json.dumps(params, indent=2, ensure_ascii=False),
                                       encoding="utf-8")
                trial_out = out_dir / f"trial_{trial_index:04d}"
                if command:
                    result = run_evaluator(command, params_path, trial_out, timeout, workdir)
                else:
                    result = {"score": None, "feasible": False,
                              "error": "spec 缺 evaluator.command（或使用 --dry-run）"}
                # 失败结果不落缓存：瞬时故障（超时/会话残留）下次重试，不把噪声固化
                if isinstance(result.get("score"), (int, float)) and result.get("feasible", True):
                    cache_path.write_text(json.dumps(result, indent=2, ensure_ascii=False),
                                          encoding="utf-8")
            score = result.get("score")
            feasible = bool(result.get("feasible", True)) and isinstance(score, (int, float))
            effective = float(score) if feasible else FAIL_SCORE
            entry = {"trial": trial_index, "params": params, "score": score,
                     "feasible": feasible, "cached": cached,
                     "duration": result.get("duration"),
                     "error": result.get("error")}
            history.append(entry)
            scores.append(effective)
            with trials_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
            if feasible and (best is None or float(score) < best["score"]):
                best = entry
            print(f"[param_search] trial {trial_index:03d} score={score} "
                  f"feasible={feasible} cached={cached} params={params}")
        if optimizer == "cmaes":
            # 预算截断时最后一代只有部分个体被评估：未评估个体用当前最差分参与更新
            if len(scores) < len(batch):
                pad = (max(scores) + 1.0) if scores else FAIL_SCORE
                scores = scores + [pad] * (len(batch) - len(scores))
            searcher.tell([vector for vector, _ in batch], scores)
        else:
            searcher.tell(batch[0][0], scores[0])

    result = {
        "search_version": SEARCH_VERSION,
        "spec": spec,
        "space": space.matrix(),
        "trials": len(history),
        "best": best,
        "score_floor": FAIL_SCORE,
        "elapsed_seconds": round(time.time() - started, 3),
        "history": history,
        "note": "best 是搜索最优；验收必须在留出 leg + 全量判读面复验（references/08-search.md）",
    }
    (out_dir / "search_result.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if best:
        (out_dir / "best_params.json").write_text(
            json.dumps(best["params"], indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        (out_dir / "best_trial.json").write_text(
            json.dumps(best, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return result


# ======================================================================================
# dry-run / self-test
# ======================================================================================

def _sphere_score(params: dict) -> float:
    return sum((value - 0.35) ** 2 for value in params.values())


def dry_run(out_dir: Path, trials: int, optimizer: str) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    space_spec = {f"x{i}": {"kind": "float", "min": 0.0, "max": 1.0, "default": 0.5}
                  for i in range(6)}
    spec = {"space": space_spec,
            "budget": {"trials": trials, "seed": 11, "optimizer": optimizer},
            "objective": {"note": "sphere: 目标全 0.35"}}
    space = Space(space_spec)
    searcher = (CMAES(space.dim, space.default_vector(), 0.25, 11) if optimizer == "cmaes"
                else TPE(space.dim, 11))
    best = None
    history = []
    for _ in range(trials):
        if optimizer == "cmaes":
            population = searcher.ask()
            batch = [(vector, space.decode(vector)) for vector in population]
        else:
            vector = searcher.ask()
            batch = [(vector, space.decode(vector))]
        scores = []
        for vector, params in batch:
            score = _sphere_score(params)
            scores.append(score)
            history.append({"params": params, "score": score})
            if best is None or score < best["score"]:
                best = {"params": params, "score": score}
        if optimizer == "cmaes":
            searcher.tell([vector for vector, _ in batch], scores)
        else:
            searcher.tell(batch[0][0], scores[0])
    result = {"mode": "dry-run", "optimizer": optimizer, "trials": trials,
              "best": best, "history": history}
    (out_dir / "dry_run_result.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return result


def _self_test() -> int:
    import tempfile
    problems = []
    with tempfile.TemporaryDirectory() as directory:
        temp = Path(directory)
        for optimizer in ("cmaes", "tpe"):
            result = dry_run(temp / optimizer, 60, optimizer)
            if result["best"]["score"] > 0.02:
                problems.append(f"{optimizer} 60 trials 未收敛: {result['best']['score']}")
        # 空间编解码：边界、choice、int step 必须无损（搜索最优才能落回可消费参数）
        space = Space({
            "f": {"kind": "float", "min": -1.0, "max": 2.0},
            "l": {"kind": "float", "min": 0.1, "max": 10.0, "log": True},
            "i": {"kind": "int", "min": 0, "max": 10, "step": 2},
            "c": {"kind": "choice", "choices": ["a", "b", "c"]},
        })
        for params in ({"f": -1.0, "l": 0.1, "i": 0, "c": "a"},
                       {"f": 2.0, "l": 10.0, "i": 10, "c": "c"},
                       {"f": 0.3, "l": 1.2, "i": 4, "c": "b"}):
            decoded = space.decode(space.encode(params))
            for key, expected in params.items():
                if key in ("f", "l"):
                    if abs(decoded[key] - expected) > 1e-4:
                        problems.append(f"encode/decode {key}: {decoded[key]} != {expected}")
                elif decoded[key] != expected:
                    problems.append(f"encode/decode {key}: {decoded[key]} != {expected}")
    if problems:
        for problem in problems:
            print(f"[FAIL] {problem}", file=sys.stderr)
        return 1
    print("[OK] param_search self-test: CMA-ES/TPE 收敛、空间编解码无损")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CMA-ES/TPE 闭环参数搜索")
    parser.add_argument("--spec", type=Path, help="search spec JSON")
    parser.add_argument("--out", type=Path, default=Path("temp/search"))
    parser.add_argument("--trials", type=int, help="覆盖 spec.budget.trials")
    parser.add_argument("--optimizer", choices=("cmaes", "tpe"), help="覆盖 spec.budget.optimizer")
    parser.add_argument("--dry-run", action="store_true", help="球函数自检，不调用评估器")
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.self_test:
        return _self_test()
    out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.dry_run:
        result = dry_run(out_dir, args.trials or 60, args.optimizer or "cmaes")
        print(json.dumps({"best": result["best"], "trials": result["trials"]},
                         indent=2, ensure_ascii=False))
        return 0
    if not args.spec:
        raise SystemExit("需要 --spec（或 --dry-run/--self-test）")
    spec = json.loads(args.spec.read_text(encoding="utf-8-sig"))
    if args.trials:
        spec.setdefault("budget", {})["trials"] = args.trials
    if args.optimizer:
        spec.setdefault("budget", {})["optimizer"] = args.optimizer
    result = optimize(spec, out_dir)
    print(json.dumps({"trials": result["trials"], "best": result["best"],
                      "elapsed_seconds": result["elapsed_seconds"]},
                     indent=2, ensure_ascii=False))
    return 0 if result["best"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
