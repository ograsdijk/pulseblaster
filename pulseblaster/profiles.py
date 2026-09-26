"""Parameterised pulse-sequence profiles.

A profile describes a repeating pulse sequence by name instead of by raw
nanosecond offsets. It has three parts:

- ``params``: named numeric variables (for example a Q-switch delay) that
  signals refer to and that a scan can override;
- ``signals``: outputs, each with a frequency, a start and a high time;
- ``gates``: masking signals that limit the outputs they cover to their own high
  windows (``masking_signals`` in :func:`generate_pulses.generate_repeating_pulses`).

Signal fields are arithmetic expressions (``+ - * /`` and parentheses) over
numbers, params, and the ``.start``, ``.end``, ``.high`` and ``.period`` of rows
defined above them, for example ``flashlamp.start + qswitch_delay_us``. Times
are in microseconds and frequencies in hertz.

:func:`resolve_profile` evaluates every expression, maps channel names to
output numbers and returns every problem it finds at once, so an editor can
show them together. :func:`to_signals` then produces the :class:`Signal` lists
the compiler takes, and :func:`program_key` identifies the compiled program.

Example profile::

    name: yag_23hz
    params:
      rep_rate_hz: {value: 23, unit: Hz}
      qswitch_delay_us: {value: 80, unit: us}
    signals:
      - name: flashlamp
        channels: [flashlamp]
        frequency_hz: rep_rate_hz
        start_us: 1000
        high_us: 100
      - name: qswitch
        channels: [qswitch]
        frequency_hz: rep_rate_hz
        start_us: flashlamp.start + qswitch_delay_us
        high_us: 100
    compile:
      optimization: basic
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from importlib import metadata
from pathlib import Path
from typing import Any, Literal

import yaml

from .data_structures import OptimizationLevel, Signal
from .validation import ESR_PRO_250, BoardProfile

PROFILE_FORMAT_VERSION = 1
REFERENCE_ATTRIBUTES = ("start", "end", "high", "period")
DEFAULT_MAX_SUPERPERIOD_S = 10.0

_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")

ItemKind = Literal["signal", "gate"]
IssueKind = Literal["param", "signal", "gate", "view", "profile"]


class ProfileError(ValueError):
    """Raised when a profile document is structurally invalid."""


class ExpressionError(ValueError):
    """Raised when an expression cannot be evaluated."""


# --------------------------------------------------------------------------- model


@dataclass(frozen=True)
class ParamSpec:
    name: str
    value: float
    unit: str = ""
    min: float | None = None
    max: float | None = None


@dataclass(frozen=True)
class ItemSpec:
    """A signal or a gate. Timing fields are expression strings."""

    kind: ItemKind
    name: str
    channels: tuple[str | int, ...]
    frequency_hz: str
    start_us: str
    high_us: str | None = None
    duty_cycle_percent: str | None = None
    active_high: bool = True


@dataclass(frozen=True)
class ViewSpec:
    name: str
    start_us: str
    stop_us: str


@dataclass(frozen=True)
class Profile:
    name: str
    params: tuple[ParamSpec, ...] = ()
    signals: tuple[ItemSpec, ...] = ()
    gates: tuple[ItemSpec, ...] = ()
    views: tuple[ViewSpec, ...] = ()
    optimization: OptimizationLevel = OptimizationLevel.BASIC
    max_superperiod_s: float = DEFAULT_MAX_SUPERPERIOD_S
    description: str = ""

    @property
    def items(self) -> tuple[ItemSpec, ...]:
        """Signals followed by gates, the order in which they are resolved."""
        return self.signals + self.gates

    def param(self, name: str) -> ParamSpec | None:
        return next((p for p in self.params if p.name == name), None)


# --------------------------------------------------------------------------- parsing


def _expr(value: Any, where: str) -> str:
    if isinstance(value, bool) or value is None:
        raise ProfileError(f"{where} must be a number or an expression")
    if isinstance(value, (int, float)):
        return repr(value) if isinstance(value, float) else str(value)
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise ProfileError(f"{where} must be a number or an expression")


def _number(value: Any, where: str, *, optional: bool = False) -> float | None:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProfileError(f"{where} must be a number")
    if not math.isfinite(float(value)):
        raise ProfileError(f"{where} must be finite")
    return float(value)


def _parse_item(raw: Any, kind: ItemKind, index: int) -> ItemSpec:
    where = f"{kind}s[{index}]"
    if not isinstance(raw, Mapping):
        raise ProfileError(f"{where} must be a mapping")
    known = {
        "name", "channels", "frequency_hz", "start_us", "high_us",
        "duty_cycle_percent", "active_high",
    }
    unknown = set(raw) - known
    if unknown:
        raise ProfileError(f"{where} has unknown keys: {sorted(unknown)}")
    name = raw.get("name")
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise ProfileError(f"{where}.name must be an identifier, got {name!r}")
    where = f"{kind} {name!r}"
    channels = raw.get("channels", [])
    if not isinstance(channels, list) or not all(
        isinstance(c, (str, int)) and not isinstance(c, bool) for c in channels
    ):
        raise ProfileError(f"{where}: channels must be a list of names or output numbers")
    has_high = raw.get("high_us") is not None
    has_duty = raw.get("duty_cycle_percent") is not None
    if has_high == has_duty:
        raise ProfileError(f"{where}: give exactly one of high_us or duty_cycle_percent")
    active_high = raw.get("active_high", True)
    if not isinstance(active_high, bool):
        raise ProfileError(f"{where}: active_high must be true or false")
    if kind == "gate" and not active_high:
        raise ProfileError(f"{where}: gates have no polarity; remove active_high")
    return ItemSpec(
        kind=kind,
        name=name,
        channels=tuple(channels),
        frequency_hz=_expr(raw.get("frequency_hz"), f"{where}.frequency_hz"),
        start_us=_expr(raw.get("start_us", 0), f"{where}.start_us"),
        high_us=_expr(raw["high_us"], f"{where}.high_us") if has_high else None,
        duty_cycle_percent=(
            _expr(raw["duty_cycle_percent"], f"{where}.duty_cycle_percent")
            if has_duty
            else None
        ),
        active_high=active_high,
    )


def profile_from_dict(data: Mapping[str, Any], *, name: str | None = None) -> Profile:
    """Build a :class:`Profile` from parsed YAML/JSON data.

    Only structure is checked here. Expressions, channel names and timing are
    checked by :func:`resolve_profile`, which reports all problems together.
    """
    if not isinstance(data, Mapping):
        raise ProfileError("a profile must be a mapping")
    known = {"name", "description", "params", "signals", "gates", "views", "compile"}
    unknown = set(data) - known
    if unknown:
        raise ProfileError(f"unknown top-level keys: {sorted(unknown)}")

    profile_name = data.get("name", name)
    if not isinstance(profile_name, str) or not _PROFILE_NAME_RE.match(profile_name):
        raise ProfileError(f"profile name must be a simple file-safe name, got {profile_name!r}")

    params: list[ParamSpec] = []
    raw_params = data.get("params") or {}
    if not isinstance(raw_params, Mapping):
        raise ProfileError("params must be a mapping of name -> {value, unit, min, max}")
    for pname, raw in raw_params.items():
        if not isinstance(pname, str) or not _NAME_RE.match(pname):
            raise ProfileError(f"param name must be an identifier, got {pname!r}")
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            raw = {"value": raw}
        if not isinstance(raw, Mapping):
            raise ProfileError(f"param {pname!r} must be a number or a mapping")
        extra = set(raw) - {"value", "unit", "min", "max"}
        if extra:
            raise ProfileError(f"param {pname!r} has unknown keys: {sorted(extra)}")
        unit = raw.get("unit", "")
        if not isinstance(unit, str):
            raise ProfileError(f"param {pname!r}: unit must be text")
        params.append(
            ParamSpec(
                name=pname,
                value=_number(raw.get("value"), f"param {pname!r}.value"),  # type: ignore[arg-type]
                unit=unit,
                min=_number(raw.get("min"), f"param {pname!r}.min", optional=True),
                max=_number(raw.get("max"), f"param {pname!r}.max", optional=True),
            )
        )

    def items(key: str, kind: ItemKind) -> tuple[ItemSpec, ...]:
        raw_list = data.get(key) or []
        if not isinstance(raw_list, list):
            raise ProfileError(f"{key} must be a list")
        return tuple(_parse_item(raw, kind, i) for i, raw in enumerate(raw_list))

    views: list[ViewSpec] = []
    raw_views = data.get("views") or []
    if not isinstance(raw_views, list):
        raise ProfileError("views must be a list")
    for i, raw in enumerate(raw_views):
        if not isinstance(raw, Mapping) or not isinstance(raw.get("name"), str):
            raise ProfileError(f"views[{i}] must be a mapping with a name")
        views.append(
            ViewSpec(
                name=raw["name"],
                start_us=_expr(raw.get("start_us", 0), f"view {raw['name']!r}.start_us"),
                stop_us=_expr(raw.get("stop_us"), f"view {raw['name']!r}.stop_us"),
            )
        )

    compile_cfg = data.get("compile") or {}
    if not isinstance(compile_cfg, Mapping):
        raise ProfileError("compile must be a mapping")
    extra = set(compile_cfg) - {"optimization", "max_superperiod_s"}
    if extra:
        raise ProfileError(f"compile has unknown keys: {sorted(extra)}")
    try:
        optimization = OptimizationLevel(compile_cfg.get("optimization", "basic"))
    except ValueError as exc:
        raise ProfileError(
            f"compile.optimization must be one of {[o.value for o in OptimizationLevel]}"
        ) from exc
    max_superperiod_s = _number(
        compile_cfg.get("max_superperiod_s", DEFAULT_MAX_SUPERPERIOD_S),
        "compile.max_superperiod_s",
    )
    description = data.get("description", "")
    if not isinstance(description, str):
        raise ProfileError("description must be text")

    return Profile(
        name=profile_name,
        params=tuple(params),
        signals=items("signals", "signal"),
        gates=items("gates", "gate"),
        views=tuple(views),
        optimization=optimization,
        max_superperiod_s=float(max_superperiod_s),  # type: ignore[arg-type]
        description=description,
    )


def parse_profile(text: str, *, name: str | None = None) -> Profile:
    """Parse profile YAML text."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ProfileError(f"invalid YAML: {exc}") from exc
    if data is None:
        raise ProfileError("the profile is empty")
    return profile_from_dict(data, name=name)


def load_profile(path: str | Path) -> Profile:
    """Load a profile file; the file stem is the default profile name."""
    path = Path(path)
    return parse_profile(path.read_text(encoding="utf-8"), name=path.stem)


def _yaml_value(expr: str) -> int | float | str:
    try:
        return int(expr)
    except ValueError:
        pass
    try:
        value = float(expr)
        return value if math.isfinite(value) else expr
    except ValueError:
        return expr


def profile_to_dict(profile: Profile) -> dict[str, Any]:
    """Plain-data form of a profile (the inverse of :func:`profile_from_dict`)."""

    def number(value: float) -> int | float:
        return int(value) if float(value).is_integer() else value

    def item(spec: ItemSpec) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": spec.name,
            "channels": list(spec.channels),
            "frequency_hz": _yaml_value(spec.frequency_hz),
            "start_us": _yaml_value(spec.start_us),
        }
        if spec.duty_cycle_percent is not None:
            out["duty_cycle_percent"] = _yaml_value(spec.duty_cycle_percent)
        else:
            out["high_us"] = _yaml_value(spec.high_us or "")
        if spec.kind == "signal" and not spec.active_high:
            out["active_high"] = False
        return out

    params: dict[str, Any] = {}
    for p in profile.params:
        entry: dict[str, Any] = {"value": number(p.value)}
        if p.unit:
            entry["unit"] = p.unit
        if p.min is not None:
            entry["min"] = number(p.min)
        if p.max is not None:
            entry["max"] = number(p.max)
        params[p.name] = entry

    out: dict[str, Any] = {"name": profile.name}
    if profile.description:
        out["description"] = profile.description
    out["params"] = params
    out["signals"] = [item(s) for s in profile.signals]
    out["gates"] = [item(g) for g in profile.gates]
    out["views"] = [
        {"name": v.name, "start_us": _yaml_value(v.start_us), "stop_us": _yaml_value(v.stop_us)}
        for v in profile.views
    ]
    compile_cfg: dict[str, Any] = {"optimization": profile.optimization.value}
    if profile.max_superperiod_s != DEFAULT_MAX_SUPERPERIOD_S:
        compile_cfg["max_superperiod_s"] = number(profile.max_superperiod_s)
    out["compile"] = compile_cfg
    return out


class _FlowList(list):  # type: ignore[type-arg]
    """Marker so short lists (channels) are written inline."""


class _FlowDict(dict):  # type: ignore[type-arg]
    """Marker so param and view entries are written inline."""


class _ProfileDumper(yaml.SafeDumper):
    pass


_ProfileDumper.add_representer(
    _FlowList,
    lambda dumper, data: dumper.represent_sequence(
        "tag:yaml.org,2002:seq", data, flow_style=True
    ),
)
_ProfileDumper.add_representer(
    _FlowDict,
    lambda dumper, data: dumper.represent_mapping(
        "tag:yaml.org,2002:map", data, flow_style=True
    ),
)


def dump_profile(profile: Profile) -> str:
    """Canonical YAML text for a profile."""
    data = profile_to_dict(profile)
    data["params"] = {k: _FlowDict(v) for k, v in data["params"].items()}
    for key in ("signals", "gates"):
        for entry in data[key]:
            entry["channels"] = _FlowList(entry["channels"])
    data["views"] = [_FlowDict(v) for v in data["views"]]
    return yaml.dump(
        data,
        Dumper=_ProfileDumper,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=100,
    )


def profile_text_hash(text: str) -> str:
    """Short identifier of profile text; changes whenever the file changes."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------- expressions


def _reference_names(expr: str) -> tuple[set[str], set[str]]:
    """Return (plain names, referenced row names) used by an expression."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return set(), set()
    names: set[str] = set()
    rows: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            rows.add(node.value.id)
        elif isinstance(node, ast.Name):
            names.add(node.id)
    return names - rows, rows


def evaluate_expression(
    expr: str,
    params: Mapping[str, float],
    rows: Mapping[str, Mapping[str, float]] | None = None,
) -> float:
    """Evaluate a timing expression.

    Allowed: numbers, param names, ``<row>.start|end|high|period``, unary minus
    and ``+ - * /`` with parentheses. Anything else raises :class:`ExpressionError`.
    """
    rows = rows or {}
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"can't parse {expr!r}") from exc

    def ev(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise ExpressionError(f"{node.value!r} is not a number")
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id in params:
                return float(params[node.id])
            if node.id in rows:
                raise ExpressionError(
                    f"{node.id!r} is a row; use {node.id}.start, .end, .high or .period"
                )
            raise ExpressionError(f"unknown name {node.id!r}")
        if isinstance(node, ast.Attribute):
            if not isinstance(node.value, ast.Name):
                raise ExpressionError("only <row>.start/.end/.high/.period is allowed")
            row = node.value.id
            if node.attr not in REFERENCE_ATTRIBUTES:
                raise ExpressionError(
                    f"{row}.{node.attr}: use .start, .end, .high or .period"
                )
            if row not in rows:
                raise ExpressionError(f"{row!r} is not a signal or gate defined above this row")
            return float(rows[row][node.attr])
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = ev(node.operand)
            return -value if isinstance(node.op, ast.USub) else value
        if isinstance(node, ast.BinOp) and isinstance(
            node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)
        ):
            left, right = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if right == 0:
                raise ExpressionError("division by zero")
            return left / right
        raise ExpressionError("only numbers, names, + - * / and parentheses are allowed")

    value = ev(tree)
    if not math.isfinite(value):
        raise ExpressionError("result is not finite")
    return value


# --------------------------------------------------------------------------- resolution


@dataclass(frozen=True)
class Issue:
    kind: IssueKind
    name: str
    message: str
    field: str | None = None
    blocking: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "field": self.field,
            "message": self.message,
            "blocking": self.blocking,
        }


@dataclass(frozen=True)
class ResolvedItem:
    kind: ItemKind
    name: str
    channel_names: tuple[str, ...]
    outputs: tuple[int, ...]
    frequency_hz: float | None
    start_us: float | None
    high_us: float | None
    period_us: float | None
    duty_cycle: float | None
    active_high: bool
    reference: tuple[str, str] | None
    valid: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "channels": list(self.channel_names),
            "outputs": list(self.outputs),
            "frequency_hz": self.frequency_hz,
            "start_us": self.start_us,
            "high_us": self.high_us,
            "period_us": self.period_us,
            "duty_cycle_percent": None if self.duty_cycle is None else self.duty_cycle * 100,
            "active_high": self.active_high,
            "reference": list(self.reference) if self.reference else None,
            "valid": self.valid,
        }


@dataclass(frozen=True)
class ResolvedView:
    name: str
    start_us: float | None
    stop_us: float | None


@dataclass(frozen=True)
class ResolvedProfile:
    profile: Profile
    params: dict[str, float]
    items: tuple[ResolvedItem, ...]
    issues: tuple[Issue, ...]
    superperiod_us: float | None
    views: tuple[ResolvedView, ...]
    board: BoardProfile = field(default=ESR_PRO_250)

    @property
    def ok(self) -> bool:
        return not any(issue.blocking for issue in self.issues)

    @property
    def signals(self) -> tuple[ResolvedItem, ...]:
        return tuple(i for i in self.items if i.kind == "signal")

    @property
    def gates(self) -> tuple[ResolvedItem, ...]:
        return tuple(i for i in self.items if i.kind == "gate")

    def param_usage(self) -> dict[str, list[str]]:
        """Which rows and views refer to each param."""
        usage: dict[str, list[str]] = {p.name: [] for p in self.profile.params}
        for spec in self.profile.items:
            exprs = [spec.frequency_hz, spec.start_us, spec.high_us, spec.duty_cycle_percent]
            used: set[str] = set()
            for expr in exprs:
                if expr is not None:
                    used |= _reference_names(expr)[0]
            for pname in used:
                if pname in usage:
                    usage[pname].append(spec.name)
        return usage

    def raise_for_issues(self) -> None:
        blocking = [i for i in self.issues if i.blocking]
        if blocking:
            details = "; ".join(f"{i.name}: {i.message}" for i in blocking)
            raise ProfileError(f"profile {self.profile.name!r} has problems: {details}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.profile.name,
            "params": dict(self.params),
            "items": [i.to_dict() for i in self.items],
            "issues": [i.to_dict() for i in self.issues],
            "ok": self.ok,
            "superperiod_us": self.superperiod_us,
            "views": [
                {"name": v.name, "start_us": v.start_us, "stop_us": v.stop_us}
                for v in self.views
            ],
            "param_usage": self.param_usage(),
            "optimization": self.profile.optimization.value,
        }


def _superperiod_us(frequencies: Sequence[float]) -> float | None:
    if not frequencies:
        return None
    fracs = [Fraction(str(f)) for f in frequencies]
    common_den = math.lcm(*(f.denominator for f in fracs))
    scaled = [f.numerator * (common_den // f.denominator) for f in fracs]
    fundamental = Fraction(math.gcd(*scaled), common_den)
    return float(Fraction(1_000_000) / fundamental)


def _format_us(value: float) -> str:
    if abs(value) >= 1e6:
        return f"{value / 1e6:.6g} s"
    if abs(value) >= 1e3:
        return f"{value / 1e3:.6g} ms"
    return f"{value:.6g} µs"


def resolve_profile(
    profile: Profile,
    channel_map: Mapping[str, int] | None = None,
    params: Mapping[str, Any] | None = None,
    board: BoardProfile = ESR_PRO_250,
) -> ResolvedProfile:
    """Evaluate a profile with optional param overrides and collect every issue."""
    channel_map = dict(channel_map or {})
    issues: list[Issue] = []

    values: dict[str, float] = {p.name: p.value for p in profile.params}
    for key, raw in (params or {}).items():
        if profile.param(key) is None:
            issues.append(Issue("param", str(key), f"the profile has no param {key!r}"))
            continue
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw):
            issues.append(Issue("param", key, f"{key} must be a finite number, got {raw!r}"))
            continue
        values[key] = float(raw)
    for pspec in profile.params:
        value = values[pspec.name]
        if pspec.min is not None and value < pspec.min:
            issues.append(
                Issue("param", pspec.name, f"{pspec.name} = {value:g} is below its minimum {pspec.min:g}")
            )
        if pspec.max is not None and value > pspec.max:
            issues.append(
                Issue("param", pspec.name, f"{pspec.name} = {value:g} is above its maximum {pspec.max:g}")
            )

    seen: set[str] = set(values)
    rows: dict[str, dict[str, float]] = {}
    resolved: list[ResolvedItem] = []
    for spec in profile.items:
        kind: IssueKind = spec.kind

        def add(message: str, field_name: str | None = None) -> None:
            issues.append(Issue(kind, spec.name, message, field_name))  # noqa: B023

        if spec.name in seen:
            add(f"the name {spec.name!r} is already used by a param or another row", "name")
        seen.add(spec.name)

        names: list[str] = []
        outputs: list[int] = []
        for ch in spec.channels:
            if isinstance(ch, int):
                out, label = ch, next((n for n, o in channel_map.items() if o == ch), str(ch))
            elif ch in channel_map:
                out, label = channel_map[ch], ch
            else:
                add(f"unknown channel {ch!r}; it isn't in the device channel map", "channels")
                continue
            if not 0 <= out < board.output_bits:
                add(f"output {out} is outside 0-{board.output_bits - 1}", "channels")
                continue
            names.append(label)
            outputs.append(out)
        if not spec.channels:
            add("no channels selected", "channels")

        def ev(expr: str | None, field_name: str) -> float | None:
            if expr is None:
                return None
            try:
                return evaluate_expression(expr, values, rows)
            except ExpressionError as exc:
                add(str(exc), field_name)  # noqa: B023
                return None

        freq = ev(spec.frequency_hz, "frequency_hz")
        start = ev(spec.start_us, "start_us")
        period = 1e6 / freq if freq is not None and freq > 0 else None
        duty: float | None = None
        if spec.duty_cycle_percent is not None:
            duty_pct = ev(spec.duty_cycle_percent, "duty_cycle_percent")
            if duty_pct is not None:
                if not 0 < duty_pct < 100:
                    add("duty cycle must be between 0 and 100 %", "duty_cycle_percent")
                else:
                    duty = duty_pct / 100
            high = duty * period if duty is not None and period is not None else None
        else:
            high = ev(spec.high_us, "high_us")

        valid = freq is not None and start is not None and high is not None
        if freq is not None and freq <= 0:
            add("frequency must be positive", "frequency_hz")
            valid = False
        if start is not None and start < 0:
            add("start can't be negative", "start_us")
            valid = False
        if high is not None and high <= 0:
            add("high must be longer than 0", "high_us")
            valid = False
        if valid and period is not None and high is not None and start is not None:
            if high >= period:
                add(
                    f"high ({_format_us(high)}) must be shorter than the period ({_format_us(period)})",
                    "high_us",
                )
                valid = False
            elif start + high > period * (1 + 1e-12):
                add(
                    "pulse runs past the end of its period: start + high = "
                    f"{_format_us(start + high)}, period = {_format_us(period)}",
                    "start_us",
                )
                valid = False

        reference = None
        _, referenced_rows = _reference_names(spec.start_us)
        if referenced_rows:
            match = re.search(
                r"(?<![\w.])([A-Za-z_]\w*)\.(start|end)\b", spec.start_us
            )
            if match and match.group(1) in rows:
                reference = (match.group(1), match.group(2))

        if start is not None and high is not None and period is not None:
            rows[spec.name] = {
                "start": start,
                "end": start + high,
                "high": high,
                "period": period,
            }
        resolved.append(
            ResolvedItem(
                kind=spec.kind,
                name=spec.name,
                channel_names=tuple(names),
                outputs=tuple(outputs),
                frequency_hz=freq,
                start_us=start,
                high_us=high,
                period_us=period,
                duty_cycle=duty,
                active_high=spec.active_high,
                reference=reference,
                valid=valid and len(outputs) == len(spec.channels) and bool(outputs),
            )
        )

    polarity: dict[int, list[ResolvedItem]] = {}
    for item in resolved:
        if item.kind == "signal":
            for out in item.outputs:
                polarity.setdefault(out, []).append(item)
    for out, users in polarity.items():
        highs = [u.name for u in users if u.active_high]
        lows = [u.name for u in users if not u.active_high]
        if highs and lows:
            label = users[0].channel_names[users[0].outputs.index(out)]
            for user in users:
                issues.append(
                    Issue(
                        "signal",
                        user.name,
                        f"{label} is active high in {', '.join(highs)} and active low in {', '.join(lows)}",
                        "active_high",
                    )
                )
    for gate in (i for i in resolved if i.kind == "gate"):
        stray = [n for n, o in zip(gate.channel_names, gate.outputs, strict=True) if o not in polarity]
        if stray:
            issues.append(
                Issue("gate", gate.name, f"gates {', '.join(stray)}, which no signal drives", "channels")
            )

    frequencies = [i.frequency_hz for i in resolved if i.frequency_hz is not None and i.frequency_hz > 0]
    superperiod = _superperiod_us(frequencies)
    if superperiod is not None and superperiod > profile.max_superperiod_s * 1e6:
        issues.append(
            Issue(
                "profile",
                profile.name,
                f"the pattern only repeats every {_format_us(superperiod)}; the limit is "
                f"{profile.max_superperiod_s:g} s. Check that the frequencies share a common divisor.",
            )
        )

    views: list[ResolvedView] = []
    for view in profile.views:
        bounds: list[float | None] = []
        for field_name, expr in (("start_us", view.start_us), ("stop_us", view.stop_us)):
            try:
                bounds.append(evaluate_expression(expr, values, rows))
            except ExpressionError as exc:
                issues.append(Issue("view", view.name, str(exc), field_name, blocking=False))
                bounds.append(None)
        views.append(ResolvedView(view.name, bounds[0], bounds[1]))

    return ResolvedProfile(
        profile=profile,
        params=values,
        items=tuple(resolved),
        issues=tuple(issues),
        superperiod_us=superperiod,
        views=tuple(views),
        board=board,
    )


# --------------------------------------------------------------------------- compilation inputs


def _signal_record(item: ResolvedItem) -> dict[str, Any]:
    """Canonical, JSON-serialisable description of one compiler input."""
    assert item.frequency_hz is not None and item.start_us is not None
    record: dict[str, Any] = {
        "frequency": repr(float(item.frequency_hz)),
        "channels": sorted(item.outputs),
        "offset_ns": round(item.start_us * 1000),
        "active_high": item.active_high,
    }
    if item.duty_cycle is not None:
        record["duty_cycle"] = repr(float(item.duty_cycle))
    else:
        assert item.high_us is not None
        record["high_ns"] = round(item.high_us * 1000)
    return record


def compiler_inputs(resolved: ResolvedProfile) -> dict[str, Any]:
    """Everything the compiler needs, as plain data (for hashing and worker processes)."""
    resolved.raise_for_issues()
    return {
        "signals": [_signal_record(i) for i in resolved.signals],
        "gates": [_signal_record(i) for i in resolved.gates],
        "optimization": resolved.profile.optimization.value,
        "max_superperiod_ns": round(resolved.profile.max_superperiod_s * 1e9),
    }


def signals_from_records(records: Sequence[Mapping[str, Any]]) -> list[Signal]:
    signals = []
    for record in records:
        kwargs: dict[str, Any] = {
            "frequency": float(record["frequency"]),
            "channels": list(record["channels"]),
            "offset": int(record["offset_ns"]),
            "active_high": bool(record["active_high"]),
        }
        if "duty_cycle" in record:
            kwargs["duty_cycle"] = float(record["duty_cycle"])
        else:
            kwargs["high"] = int(record["high_ns"])
        signals.append(Signal(**kwargs))
    return signals


def to_signals(resolved: ResolvedProfile) -> tuple[list[Signal], list[Signal]]:
    """Signals and masking signals for :func:`generate_pulses.generate_repeating_pulses`."""
    inputs = compiler_inputs(resolved)
    return signals_from_records(inputs["signals"]), signals_from_records(inputs["gates"])


def package_version() -> str:
    try:
        return metadata.version("pulseblaster")
    except metadata.PackageNotFoundError:  # pragma: no cover - running from a source tree
        return "unknown"


def program_key(resolved: ResolvedProfile) -> str:
    """Identify a compiled program.

    Built from the compiler inputs rather than the profile text, so reformatting
    a file or renaming a param keeps the same key when the timing is identical.
    The package version is included so a compiler change never reuses old output.
    """
    board = resolved.board
    payload = {
        "format": PROFILE_FORMAT_VERSION,
        "pulseblaster": package_version(),
        "board": [board.name, board.clock_mhz, board.flag_bits, board.max_program_instructions],
        **compiler_inputs(resolved),
    }
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
