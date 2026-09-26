"""Tests for parameterised profiles, the compiled-program cache and wrap rejection."""

import json

import pytest

from pulseblaster import Signal, generate_pulses, profiles, program_cache

CHANNELS = {
    "yag_trigger": 0,
    "flashlamp": 1,
    "qswitch": 2,
    "shutter": 3,
    "flashlamp_mon": 4,
    "qswitch_mon": 5,
    "shutter_mon": 6,
    "trigger_mon": 7,
    "carrier": 8,
}

YAG = """
name: yag_23hz_carrier
params:
  rep_rate_hz: {value: 23, unit: Hz}
  trigger_delay_us: {value: 1000, unit: us}
  qswitch_delay_us: {value: 80, unit: us, min: 20, max: 300}
signals:
  - {name: trigger, channels: [yag_trigger, trigger_mon], frequency_hz: rep_rate_hz, start_us: 0, high_us: 100}
  - {name: flashlamp, channels: [flashlamp, flashlamp_mon], frequency_hz: rep_rate_hz, start_us: trigger_delay_us, high_us: 100}
  - {name: qswitch, channels: [qswitch, qswitch_mon], frequency_hz: rep_rate_hz, start_us: flashlamp.start + qswitch_delay_us, high_us: 100}
  - {name: shutter, channels: [shutter, shutter_mon], frequency_hz: rep_rate_hz / 2, start_us: trigger.period - 3000, high_us: trigger.period}
  - {name: carrier, channels: [carrier], frequency_hz: 100000, start_us: 0, duty_cycle_percent: 50}
views:
  - {name: YAG timing, start_us: 0, stop_us: qswitch.end + 200}
"""

SMALL = """
name: small
params: {rep_rate_hz: {value: 1000}, delay_us: {value: 50}}
signals:
  - {name: a, channels: [0], frequency_hz: rep_rate_hz, start_us: 0, high_us: 10}
  - {name: b, channels: [1], frequency_hz: rep_rate_hz, start_us: a.end + delay_us, high_us: 20}
"""


def resolve(text: str = YAG, params=None, channel_map=CHANNELS):
    return profiles.resolve_profile(profiles.parse_profile(text), channel_map, params)


def messages(resolved) -> list[str]:
    return [issue.message for issue in resolved.issues]


# ----------------------------------------------------------------- wrap rejection


def test_signal_rejects_pulse_running_past_its_period():
    with pytest.raises(ValueError, match="past the end of its period"):
        Signal(frequency=1000, channels=[0], offset=800_000, high=400_000)


def test_signal_allows_pulse_ending_exactly_at_period_end():
    Signal(frequency=1000, channels=[0], offset=600_000, high=400_000)


def test_signal_rejects_offset_beyond_period_with_duty_cycle():
    with pytest.raises(ValueError, match="past the end of its period"):
        Signal(frequency=1000, channels=[0], offset=1_200_000, duty_cycle=0.1)


def test_compiler_rejects_signal_mutated_to_wrap():
    signal = Signal(frequency=1000, channels=[0], offset=100_000, high=400_000)
    signal.offset = 800_000
    with pytest.raises(ValueError, match="past the end of its period"):
        generate_pulses.generate_repeating_pulses([signal], progress=False)


# ----------------------------------------------------------------- expressions


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        ("1000", 1000.0),
        ("a + 2 * b", 7.0),
        ("-(a - b) / 2", 1.0),
        ("x.start + x.high", 15.0),
        ("x.period - a", 99.0),
    ],
)
def test_evaluate_expression(expr, expected):
    rows = {"x": {"start": 5.0, "end": 15.0, "high": 10.0, "period": 100.0}}
    assert profiles.evaluate_expression(expr, {"a": 1.0, "b": 3.0}, rows) == expected


@pytest.mark.parametrize(
    ("expr", "match"),
    [
        ("nope + 1", "unknown name 'nope'"),
        ("x.width", r"use \.start"),
        ("y.start", "not a signal or gate defined above"),
        ("x", "is a row"),
        ("__import__('os')", "only numbers"),
        ("a ** 2", "only numbers"),
        ("1 / 0", "division by zero"),
        ("1 +", "can't parse"),
    ],
)
def test_evaluate_expression_rejects(expr, match):
    rows = {"x": {"start": 5.0, "end": 15.0, "high": 10.0, "period": 100.0}}
    with pytest.raises(profiles.ExpressionError, match=match):
        profiles.evaluate_expression(expr, {"a": 1.0}, rows)


# ----------------------------------------------------------------- parsing


def test_parse_and_dump_round_trip():
    profile = profiles.parse_profile(YAG)
    again = profiles.parse_profile(profiles.dump_profile(profile))
    assert again == profile


def test_load_profile_uses_file_stem_as_default_name(tmp_path):
    path = tmp_path / "from_file.yaml"
    path.write_text(SMALL.replace("name: small\n", ""), encoding="utf-8")
    assert profiles.load_profile(path).name == "from_file"


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("name: x\nsignals: [{name: a, channels: [0], frequency_hz: 1, high_us: 1, duty_cycle_percent: 5}]", "exactly one"),
        ("name: x\nsignals: [{name: 1a, channels: [0], frequency_hz: 1, high_us: 1}]", "identifier"),
        ("name: x\nbogus: 1", "unknown top-level"),
        ("name: x\ncompile: {optimization: turbo}", "optimization"),
        ("name: x\ngates: [{name: g, channels: [0], frequency_hz: 1, high_us: 1, active_high: false}]", "polarity"),
        ("name: x\nparams: {p: {value: fast}}", "must be a number"),
        ("", "empty"),
        ("name: [", "invalid YAML"),
    ],
)
def test_parse_profile_rejects_structure(text, match):
    with pytest.raises(profiles.ProfileError, match=match):
        profiles.parse_profile(text)


# ----------------------------------------------------------------- resolution


def test_resolve_yag_profile():
    r = resolve()
    assert r.ok, messages(r)
    by_name = {i.name: i for i in r.items}
    assert by_name["qswitch"].start_us == pytest.approx(1080)
    assert by_name["qswitch"].outputs == (2, 5)
    assert by_name["qswitch"].reference == ("flashlamp", "start")
    assert by_name["shutter"].frequency_hz == pytest.approx(11.5)
    assert by_name["carrier"].high_us == pytest.approx(5)
    assert r.superperiod_us == pytest.approx(2e6)
    assert r.views[0].stop_us == pytest.approx(1380)
    assert r.param_usage()["qswitch_delay_us"] == ["qswitch"]


def test_param_override_moves_dependent_signal():
    r = resolve(params={"qswitch_delay_us": 95})
    assert {i.name: i for i in r.items}["qswitch"].start_us == pytest.approx(1095)


def test_resolve_reports_all_problems_together():
    r = resolve(params={"qswitch_delay_us": 43000, "unknown": 1})
    text = " | ".join(messages(r))
    assert not r.ok
    assert "has no param 'unknown'" in text
    assert "above its maximum" in text
    assert "runs past the end of its period" in text


@pytest.mark.parametrize(
    ("replace", "match"),
    [
        (("high_us: 20", "high_us: 1000"), "shorter than the period"),
        (("start_us: 0,", "start_us: -5,"), "can't be negative"),
        (("channels: [1]", "channels: [missing]"), "unknown channel 'missing'"),
        (("channels: [1]", "channels: [25]"), "outside 0-20"),
        (("channels: [1]", "channels: []"), "no channels"),
        (("name: b", "name: a"), "already used"),
        (("name: b", "name: delay_us"), "already used"),
        (("a.end + delay_us", "b.end"), "not a signal or gate defined above"),
        (("frequency_hz: rep_rate_hz, start_us: a", "frequency_hz: 0, start_us: a"), "positive"),
    ],
)
def test_resolve_item_problems(replace, match):
    r = resolve(SMALL.replace(*replace), channel_map={})
    assert not r.ok
    assert any(match in m for m in messages(r)), messages(r)


def test_resolve_polarity_conflict_and_stray_gate():
    text = """
name: x
signals:
  - {name: a, channels: [0], frequency_hz: 100, start_us: 0, high_us: 10}
  - {name: b, channels: [0], frequency_hz: 100, start_us: 50, high_us: 10, active_high: false}
gates:
  - {name: g, channels: [3], frequency_hz: 10, start_us: 0, high_us: 1000}
"""
    r = resolve(text, channel_map={})
    joined = " | ".join(messages(r))
    assert "active high in a and active low in b" in joined
    assert "which no signal drives" in joined


def test_resolve_superperiod_limit():
    text = """
name: x
signals:
  - {name: a, channels: [0], frequency_hz: 23.0001, start_us: 0, high_us: 10}
  - {name: b, channels: [1], frequency_hz: 17, start_us: 0, high_us: 10}
"""
    r = resolve(text, channel_map={})
    assert any("only repeats every" in m for m in messages(r))


def test_view_errors_do_not_block_compiling():
    r = resolve(YAG.replace("qswitch.end + 200", "nothing.end"))
    assert r.ok
    assert [i.kind for i in r.issues] == ["view"]


# ----------------------------------------------------------------- keys and signals


def test_program_key_ignores_formatting_but_tracks_timing():
    base = profiles.program_key(resolve())
    reformatted = profiles.parse_profile(profiles.dump_profile(profiles.parse_profile(YAG)))
    assert profiles.program_key(profiles.resolve_profile(reformatted, CHANNELS)) == base
    renamed = YAG.replace("qswitch_delay_us", "qsw_us")
    assert profiles.program_key(resolve(renamed)) == base
    assert profiles.program_key(resolve(params={"qswitch_delay_us": 95})) != base
    advanced = YAG + "compile: {optimization: advanced}\n"
    assert profiles.program_key(resolve(advanced)) != base


def test_program_key_refuses_invalid_profile():
    with pytest.raises(profiles.ProfileError, match="has problems"):
        profiles.program_key(resolve(params={"qswitch_delay_us": 43000}))


def test_to_signals_matches_hand_written_signals():
    signals, gates = profiles.to_signals(resolve())
    assert gates == []
    qswitch = signals[2]
    assert (qswitch.frequency, sorted(qswitch.channels), qswitch.offset, qswitch.high) == (
        23.0,
        [2, 5],
        1_080_000,
        100_000,
    )
    assert signals[4].duty_cycle == pytest.approx(0.5)


# ----------------------------------------------------------------- cache


def test_compile_to_cache_round_trip(tmp_path):
    r = resolve(SMALL, channel_map={})
    key = profiles.program_key(r)
    summary = program_cache.compile_to_cache(str(tmp_path), key, profiles.compiler_inputs(r), "ESR_PRO_250", {"profile": "small"})
    cache = program_cache.ProgramCache(tmp_path)
    entry = cache.load(key)
    assert entry is not None
    assert summary["stored_instructions"] == len(entry["instructions"])
    assert entry["meta"] == {"profile": "small"}
    assert not list(tmp_path.glob("*.tmp"))
    board = profiles.ESR_PRO_250
    instructions = program_cache.rows_to_instructions(entry["instructions"], board)
    assert program_cache.instructions_to_rows(instructions) == entry["instructions"]


def test_cache_info_and_clear_other_versions(tmp_path):
    r = resolve(SMALL, channel_map={})
    key = profiles.program_key(r)
    program_cache.compile_to_cache(str(tmp_path), key, profiles.compiler_inputs(r), "ESR_PRO_250")
    stale = json.loads((tmp_path / f"{key}.json").read_text())
    stale["pulseblaster_version"] = "0.0.1"
    stale["key"] = "old"
    (tmp_path / "old.json").write_text(json.dumps(stale))

    info = program_cache.ProgramCache(tmp_path).info()
    assert info["count"] == 2
    assert info["other_version_count"] == 1
    assert program_cache.ProgramCache(tmp_path).clear(other_versions_only=True)["removed"] == 1
    assert program_cache.ProgramCache(tmp_path).has(key)
    assert program_cache.ProgramCache(tmp_path).clear()["removed"] == 1
    assert program_cache.ProgramCache(tmp_path).info()["count"] == 0


def test_cache_ignores_corrupt_entry(tmp_path):
    (tmp_path / "abc.json").write_text("{not json")
    assert program_cache.ProgramCache(tmp_path).load("abc") is None


def test_compiled_timeline_edges(tmp_path):
    r = resolve(SMALL, channel_map={})
    key = profiles.program_key(r)
    program_cache.compile_to_cache(str(tmp_path), key, profiles.compiler_inputs(r), "ESR_PRO_250")
    rows = program_cache.ProgramCache(tmp_path).load(key)["instructions"]
    tl = program_cache.compiled_timeline(rows, "ESR_PRO_250", [0, 1], 0, 2_000_000)
    assert tl["total_ns"] == 1_000_000
    assert tl["outputs"]["1"]["edges"][:2] == [[60_000.0, 1], [80_000.0, 0]]
    # The first pass's rise is at the window start, so it is the initial level.
    assert tl["outputs"]["0"] == {
        "initial": 1,
        "edges": [[10_000.0, 0], [1_000_000.0, 1], [1_010_000.0, 0]],
    }
    dense = program_cache.compiled_timeline(rows, "ESR_PRO_250", [0], 0, 1e9, max_edges=100)
    assert dense["outputs"]["0"] == {"dense": True}
