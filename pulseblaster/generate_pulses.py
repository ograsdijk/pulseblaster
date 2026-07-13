"""
Pulse sequence generation for PulseBlaster devices.

This module provides functions for generating repeating pulse sequences
with multiple frequencies and converting them to PulseBlaster instructions.
"""

import heapq
import logging
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, replace
from fractions import Fraction
from itertools import groupby
from math import gcd, isqrt, lcm
from time import perf_counter

from .data_structures import (
    CompilationReport,
    Instruction,
    InstructionSequence,
    Opcode,
    OptimizationLevel,
    Pulse,
    Signal,
)
from .utils import round_to_nearest_n_ns
from .validation import (
    ESR_PRO_250,
    BoardProfile,
    _maximum_subroutine_depth,
    set_control_mode,
    validate_sequence,
)


@dataclass(frozen=True)
class WaveformInterval:
    """One constant-output interval expressed in hardware clock ticks."""

    flags: tuple[int, ...]
    duration_ticks: int


@dataclass(frozen=True)
class RawNode:
    interval: WaveformInterval


@dataclass(frozen=True)
class SequenceNode:
    children: tuple["CompilerNode", ...]


@dataclass(frozen=True)
class RepeatNode:
    body: SequenceNode
    count: int


@dataclass(frozen=True)
class LongDelayNode:
    interval: WaveformInterval
    multiplier: int


@dataclass(frozen=True)
class SubroutineCallNode:
    interval: WaveformInterval
    subroutine_id: int


@dataclass(frozen=True)
class BranchNode:
    interval: WaveformInterval
    target: int = 0


CompilerNode = (
    RawNode
    | SequenceNode
    | RepeatNode
    | LongDelayNode
    | SubroutineCallNode
    | BranchNode
)


def _round_fraction(value: Fraction) -> int:
    """Round a non-negative fraction to the nearest integer, halves upward."""
    if value < 0:
        raise ValueError(f"Cannot round negative timing value {value}")
    return (2 * value.numerator + value.denominator) // (2 * value.denominator)


def _round_ratio(numerator: int, denominator: int) -> int:
    return (2 * numerator + denominator) // (2 * denominator)


def _as_frequency_fraction(frequency: float) -> Fraction:
    """Preserve the decimal frequency supplied by the user as a rational value."""
    return Fraction(str(frequency))


def _frequency_gcd(frequencies: list[Fraction]) -> Fraction:
    """Greatest common divisor for positive rational frequencies."""
    common_denominator = lcm(*(frequency.denominator for frequency in frequencies))
    scaled = [
        frequency.numerator * (common_denominator // frequency.denominator)
        for frequency in frequencies
    ]
    return Fraction(gcd(*scaled), common_denominator)


def _superperiod_ticks(
    signals: list[Signal],
    masking_signals: list[Signal],
    profile: BoardProfile,
    max_superperiod_ns: int = int(10e9),
) -> int:
    frequencies = [
        _as_frequency_fraction(signal.frequency)
        for signal in [*signals, *masking_signals]
    ]
    fundamental_frequency = _frequency_gcd(frequencies)
    duration_ns = Fraction(1_000_000_000, 1) / fundamental_frequency
    if duration_ns > max_superperiod_ns:
        raise ValueError(
            "Common timespan of input frequencies is too large, "
            f"{float(duration_ns) * 1e-9:.1e} s"
        )
    return _round_fraction(duration_ns / profile.clock_period_ns)


def _signal_event_times(
    signal: Signal,
    duration_ticks: int,
    tick_ns: int,
) -> list[tuple[int, bool]]:
    """Return quantized rise/fall events for one signal over a superperiod."""
    return list(_iter_signal_events(signal, duration_ticks, tick_ns))


def _iter_signal_events(
    signal: Signal,
    duration_ticks: int,
    tick_ns: int,
) -> Iterator[tuple[int, bool]]:
    """Yield quantized rise/fall events without materializing a signal timeline."""
    frequency = _as_frequency_fraction(signal.frequency)
    period_ticks = Fraction(1_000_000_000, tick_ns) / frequency
    offset_ticks = Fraction(signal.offset, tick_ns)
    high_ticks = (
        period_ticks * Fraction(str(signal.duty_cycle))
        if signal._duty_cycle_set
        else Fraction(signal.high, tick_ns)
    )
    if high_ticks <= 0 or high_ticks >= period_ticks:
        raise ValueError(
            f"Signal on channels {signal.channels} has invalid quantized high time"
        )

    denominator = lcm(
        period_ticks.denominator,
        offset_ticks.denominator,
        high_ticks.denominator,
    )
    period_numerator = period_ticks.numerator * (
        denominator // period_ticks.denominator
    )
    rise_numerator = offset_ticks.numerator * (
        denominator // offset_ticks.denominator
    )
    high_numerator = high_ticks.numerator * (
        denominator // high_ticks.denominator
    )
    duration_numerator = duration_ticks * denominator
    while rise_numerator < duration_numerator:
        rise = _round_ratio(rise_numerator, denominator)
        fall = _round_ratio(rise_numerator + high_numerator, denominator)
        if fall <= rise:
            raise ValueError(
                f"Signal on channels {signal.channels} has a pulse shorter than one "
                f"{tick_ns} ns clock tick"
            )
        if rise < duration_ticks:
            yield rise, True
        if fall < duration_ticks:
            yield fall, False
        rise_numerator += period_numerator


def _append_interval(
    intervals: list[WaveformInterval], flags: tuple[int, ...], duration: int
) -> None:
    if duration <= 0:
        return
    if intervals and intervals[-1].flags == flags:
        previous = intervals[-1]
        intervals[-1] = WaveformInterval(
            flags, previous.duration_ticks + duration
        )
    else:
        intervals.append(WaveformInterval(flags, duration))


def _tagged_signal_events(
    signal: Signal,
    duration_ticks: int,
    tick_ns: int,
    is_mask: bool,
    signal_idx: int,
) -> Iterator[tuple[int, bool, int, bool]]:
    for tick, active in _iter_signal_events(signal, duration_ticks, tick_ns):
        yield tick, is_mask, signal_idx, active


def _reference_intervals(
    signals: list[Signal],
    masking_signals: list[Signal],
    profile: BoardProfile,
    max_superperiod_ns: int = int(10e9),
) -> tuple[list[WaveformInterval], int]:
    """Build a clock-quantized waveform by visiting output transitions only."""
    duration_ticks = _superperiod_ticks(
        signals, masking_signals, profile, max_superperiod_ns
    )
    tick_ns = profile.clock_period_ns
    active_signals = [False] * len(signals)
    active_masks = [False] * len(masking_signals)
    channel_polarities: dict[int, bool] = {}
    for signal in signals:
        for channel in signal.channels:
            existing_polarity = channel_polarities.setdefault(
                channel, signal.active_high
            )
            if existing_polarity != signal.active_high:
                raise ValueError(
                    f"Channel {channel} is used with conflicting active_high values"
                )

    mask_indices_by_channel: dict[int, list[int]] = defaultdict(list)
    for idx, signal in enumerate(masking_signals):
        for channel in signal.channels:
            mask_indices_by_channel[channel].append(idx)

    intervals: list[WaveformInterval] = []

    def flags_for_state() -> tuple[int, ...]:
        logical_channels = {
            channel
            for signal_idx, signal in enumerate(signals)
            if active_signals[signal_idx]
            for channel in signal.channels
        }
        gated_channels = {
            channel
            for channel in logical_channels
            if not mask_indices_by_channel[channel]
            or all(active_masks[idx] for idx in mask_indices_by_channel[channel])
        }
        flags = [0] * profile.flag_bits
        for channel, active_high in channel_polarities.items():
            logical_active = channel in gated_channels
            flags[channel] = int(logical_active if active_high else not logical_active)
        set_control_mode(flags, profile.generated_disable_mode, profile)
        return tuple(flags)

    event_sources: list[Iterator[tuple[int, bool, int, bool]]] = []
    for idx, signal in enumerate(signals):
        event_sources.append(
            _tagged_signal_events(
                signal, duration_ticks, tick_ns, False, idx
            )
        )
    for idx, signal in enumerate(masking_signals):
        event_sources.append(
            _tagged_signal_events(
                signal, duration_ticks, tick_ns, True, idx
            )
        )

    last_tick = 0
    merged_events = heapq.merge(*event_sources)
    for tick, simultaneous in groupby(merged_events, key=lambda event: event[0]):
        _append_interval(intervals, flags_for_state(), tick - last_tick)
        for _, is_mask, signal_idx, active in simultaneous:
            if is_mask:
                active_masks[signal_idx] = active
            else:
                active_signals[signal_idx] = active
        last_tick = tick

    _append_interval(intervals, flags_for_state(), duration_ticks - last_tick)

    return intervals, duration_ticks


def _repeat_count(
    intervals: list[WaveformInterval], start: int, pattern_length: int, stop: int
) -> int:
    """Count consecutive copies of a pattern without allocating list slices."""
    repetitions = 1
    while start + (repetitions + 1) * pattern_length <= stop:
        candidate_start = start + repetitions * pattern_length
        if any(
            intervals[start + offset] != intervals[candidate_start + offset]
            for offset in range(pattern_length)
        ):
            break
        repetitions += 1
    return repetitions


def _best_repeat(
    intervals: list[WaveformInterval],
    start: int,
    stop: int,
    profile: BoardProfile,
    max_pattern_length: int = 32,
) -> tuple[int, int] | None:
    """Find the most profitable adjacent repeat beginning at ``start``."""
    best: tuple[int, int] | None = None
    best_savings = 0
    maximum = min(max_pattern_length, (stop - start) // 2)
    candidate_lengths = list(range(2, min(maximum, 32) + 1))
    if maximum > 32:
        candidate_lengths.extend(
            length
            for length in (48, 64, 96, 128, 192, 256, 384, 512)
            if length <= maximum
        )
    for pattern_length in candidate_lengths:
        repetitions = _repeat_count(intervals, start, pattern_length, stop)
        if repetitions < 2:
            continue
        pattern = intervals[start : start + pattern_length]
        if not (
            profile.minimum_cycles_for(Opcode.LOOP)
            <= pattern[0].duration_ticks
            <= profile.max_delay_cycles
        ):
            continue
        if not (
            profile.minimum_cycles_for(Opcode.END_LOOP)
            <= pattern[-1].duration_ticks
            <= profile.max_delay_cycles
        ):
            continue
        if any(
            item.duration_ticks < profile.minimum_cycles_for(Opcode.CONTINUE)
            for item in pattern[1:-1]
        ):
            continue
        savings = (repetitions - 1) * pattern_length
        if savings > best_savings:
            best = pattern_length, repetitions
            best_savings = savings
    return best


def _instruction(
    interval: WaveformInterval,
    profile: BoardProfile,
    opcode: Opcode = Opcode.CONTINUE,
    inst_data: int = 0,
) -> Instruction:
    return Instruction(
        label="",
        flags=list(interval.flags),
        duration=interval.duration_ticks * profile.clock_period_ns,
        opcode=opcode,
        inst_data=inst_data,
    )


def _split_repeat_nodes(body: SequenceNode, repetitions: int, limit: int) -> list[CompilerNode]:
    nodes: list[CompilerNode] = []
    while repetitions:
        chunk = min(repetitions, limit)
        if chunk == 1:
            nodes.extend(body.children)
        else:
            nodes.append(RepeatNode(body, chunk))
        repetitions -= chunk
    return nodes


def _build_sequence_ir(
    intervals: list[WaveformInterval],
    profile: BoardProfile,
    *,
    advanced: bool,
    depth: int = 0,
) -> SequenceNode:
    """Build a deterministic symbolic plan from adjacent repeated blocks."""
    if advanced and 4 <= len(intervals) <= 128:
        return _build_sequence_ir_dp(intervals, profile, depth)
    nodes: list[CompilerNode] = []
    idx = 0
    stop = len(intervals)
    max_pattern_length = 512 if advanced else 32
    while idx < stop:
        repeat = _best_repeat(
            intervals,
            idx,
            stop,
            profile,
            max_pattern_length=max_pattern_length,
        )
        if repeat is None:
            nodes.append(RawNode(intervals[idx]))
            idx += 1
            continue

        pattern_length, repetitions = repeat
        pattern = intervals[idx : idx + pattern_length]
        if advanced and depth + 1 < profile.max_loop_depth and pattern_length > 2:
            middle = _build_sequence_ir(
                pattern[1:-1], profile, advanced=True, depth=depth + 1
            )
            body = SequenceNode(
                (RawNode(pattern[0]), *middle.children, RawNode(pattern[-1]))
            )
        else:
            body = SequenceNode(tuple(RawNode(item) for item in pattern))
        nodes.extend(
            _split_repeat_nodes(body, repetitions, profile.max_loop_iterations)
        )
        idx += pattern_length * repetitions
    return SequenceNode(tuple(nodes))


def _node_word_cost(node: CompilerNode) -> int:
    if isinstance(node, SequenceNode):
        return sum(_node_word_cost(child) for child in node.children)
    if isinstance(node, RepeatNode):
        return _node_word_cost(node.body)
    return 1


def _build_sequence_ir_dp(
    intervals: list[WaveformInterval], profile: BoardProfile, depth: int
) -> SequenceNode:
    """Choose the minimum-word encoding for a bounded waveform window."""
    count = len(intervals)
    costs = [0] * (count + 1)
    plans: list[tuple[CompilerNode, ...]] = [tuple() for _ in range(count + 1)]
    for idx in range(count - 1, -1, -1):
        costs[idx] = 1 + costs[idx + 1]
        plans[idx] = (RawNode(intervals[idx]), *plans[idx + 1])
        maximum = min(64, (count - idx) // 2)
        for pattern_length in range(2, maximum + 1):
            repetitions = _repeat_count(intervals, idx, pattern_length, count)
            if repetitions < 2:
                continue
            pattern = intervals[idx : idx + pattern_length]
            if not (
                profile.minimum_cycles_for(Opcode.LOOP)
                <= pattern[0].duration_ticks
                <= profile.max_delay_cycles
                and profile.minimum_cycles_for(Opcode.END_LOOP)
                <= pattern[-1].duration_ticks
                <= profile.max_delay_cycles
            ):
                continue
            if depth + 1 < profile.max_loop_depth and pattern_length > 2:
                middle = _build_sequence_ir(
                    pattern[1:-1], profile, advanced=True, depth=depth + 1
                )
                body = SequenceNode(
                    (RawNode(pattern[0]), *middle.children, RawNode(pattern[-1]))
                )
            else:
                body = SequenceNode(tuple(RawNode(item) for item in pattern))
            repeat_nodes = tuple(
                _split_repeat_nodes(
                    body, repetitions, profile.max_loop_iterations
                )
            )
            next_idx = idx + pattern_length * repetitions
            candidate_cost = sum(_node_word_cost(node) for node in repeat_nodes) + costs[
                next_idx
            ]
            if candidate_cost < costs[idx]:
                costs[idx] = candidate_cost
                plans[idx] = (*repeat_nodes, *plans[next_idx])
    return SequenceNode(plans[0])


def _exact_long_delay_factor(
    duration_ticks: int, profile: BoardProfile
) -> tuple[int, int] | None:
    """Find a legal exact base/multiplier pair for one LONG_DELAY word."""
    minimum = profile.minimum_cycles_for(Opcode.LONG_DELAY)
    if duration_ticks % profile.max_delay_cycles == 0:
        multiplier = duration_ticks // profile.max_delay_cycles
        if 2 <= multiplier <= profile.max_inst_data:
            return profile.max_delay_cycles, multiplier
    # Exhaustive factor searches become counterproductive for the 52-bit effective
    # LONG_DELAY range. Small factors cover the common exact encodings; the general
    # emitter below supplies an exact multiword representation for every legal case.
    for divisor in range(2, min(isqrt(duration_ticks), 4_096) + 1):
        if duration_ticks % divisor:
            continue
        pairs = (
            (duration_ticks // divisor, divisor),
            (divisor, duration_ticks // divisor),
        )
        for base, multiplier in pairs:
            if (
                minimum <= base <= profile.max_delay_cycles
                and 2 <= multiplier <= profile.max_inst_data
            ):
                return base, multiplier
    return None


def _emit_stable_duration(
    interval: WaveformInterval,
    instructions: list[Instruction],
    profile: BoardProfile,
) -> None:
    """Emit an exact stable duration using CONTINUE and LONG_DELAY words."""
    remaining = interval.duration_ticks
    minimum_tail = profile.minimum_cycles_for(Opcode.CONTINUE)
    minimum_long_delay = profile.minimum_cycles_for(Opcode.LONG_DELAY)
    while remaining > profile.max_delay_cycles:
        factor = _exact_long_delay_factor(remaining, profile)
        if factor is None:
            multiplier = min(
                profile.max_inst_data,
                (remaining - minimum_tail) // profile.max_delay_cycles,
            )
            if multiplier >= 2:
                base = profile.max_delay_cycles
            else:
                multiplier = 2
                base = (remaining - minimum_tail) // multiplier
            if not minimum_long_delay <= base <= profile.max_delay_cycles:
                raise ValueError(
                    f"Stable duration {interval.duration_ticks} cycles has no legal "
                    "LONG_DELAY decomposition for this board profile"
                )
            effective = base * multiplier
        else:
            base, multiplier = factor
            effective = remaining
        instructions.append(
            _instruction(
                WaveformInterval(interval.flags, base),
                profile,
                Opcode.LONG_DELAY,
                multiplier,
            )
        )
        remaining -= effective

    if remaining:
        instructions.append(
            _instruction(
                WaveformInterval(interval.flags, remaining), profile
            )
        )


def _lower_ir_node(
    node: CompilerNode,
    instructions: list[Instruction],
    profile: BoardProfile,
) -> None:
    if isinstance(node, RawNode):
        _emit_stable_duration(node.interval, instructions, profile)
    elif isinstance(node, LongDelayNode):
        instructions.append(
            _instruction(
                node.interval, profile, Opcode.LONG_DELAY, node.multiplier
            )
        )
    elif isinstance(node, SequenceNode):
        for child in node.children:
            _lower_ir_node(child, instructions, profile)
    elif isinstance(node, RepeatNode):
        if len(node.body.children) < 2:
            raise ValueError("Hardware loop body must contain at least two intervals")
        first, *middle, last = node.body.children
        if not isinstance(first, RawNode) or not isinstance(last, RawNode):
            raise ValueError("Hardware loop boundaries must be raw intervals")
        loop_address = len(instructions)
        instructions.append(
            _instruction(first.interval, profile, Opcode.LOOP, node.count)
        )
        for child in middle:
            _lower_ir_node(child, instructions, profile)
        instructions.append(
            _instruction(last.interval, profile, Opcode.END_LOOP, loop_address)
        )
    elif isinstance(node, BranchNode):
        _emit_final_branch(node.interval, instructions, profile, node.target)
    elif isinstance(node, SubroutineCallNode):
        instructions.append(
            _instruction(node.interval, profile, Opcode.JSR, node.subroutine_id)
        )
    else:  # pragma: no cover - exhaustive defensive guard
        raise TypeError(f"Unsupported compiler node {type(node)}")


def _emit_final_branch(
    interval: WaveformInterval,
    instructions: list[Instruction],
    profile: BoardProfile,
    target: int = 0,
) -> None:
    if interval.duration_ticks <= profile.max_delay_cycles:
        instructions.append(
            _instruction(interval, profile, Opcode.BRANCH, target)
        )
        return
    branch_ticks = profile.minimum_cycles_for(Opcode.BRANCH)
    _emit_stable_duration(
        WaveformInterval(interval.flags, interval.duration_ticks - branch_ticks),
        instructions,
        profile,
    )
    instructions.append(
        _instruction(
            WaveformInterval(interval.flags, branch_ticks),
            profile,
            Opcode.BRANCH,
            target,
        )
    )


def _lower_ir(
    root: SequenceNode, profile: BoardProfile
) -> list[Instruction]:
    instructions: list[Instruction] = []
    for child in root.children:
        _lower_ir_node(child, instructions, profile)
    return instructions


def _compile_with_loops(
    intervals: list[WaveformInterval],
    profile: BoardProfile,
    *,
    advanced: bool,
) -> list[Instruction]:
    if not intervals:
        raise ValueError("Waveform contains no intervals")
    body = _build_sequence_ir(intervals[:-1], profile, advanced=advanced)
    root = SequenceNode((*body.children, BranchNode(intervals[-1])))
    return _lower_ir(root, profile)


def _compile_without_loops(
    intervals: list[WaveformInterval], profile: BoardProfile
) -> list[Instruction]:
    if not intervals:
        raise ValueError("Waveform contains no intervals")
    root = SequenceNode(
        (
            *(RawNode(interval) for interval in intervals[:-1]),
            BranchNode(intervals[-1]),
        )
    )
    return _lower_ir(root, profile)


def _compress_intervals(
    intervals: list[WaveformInterval], profile: BoardProfile
) -> list[Instruction]:
    """Backward-compatible entry point for basic adjacent-loop compression."""
    return _compile_with_loops(intervals, profile, advanced=False)


def _instruction_signature(instruction: Instruction) -> tuple[tuple[int, ...], int]:
    return tuple(instruction.flags), instruction.duration


def _deduplicate_one_subroutine(
    instructions: list[Instruction], profile: BoardProfile
) -> list[Instruction]:
    """Share the most profitable repeated non-adjacent CONTINUE block."""
    branch_idx = next(
        (
            idx
            for idx, instruction in enumerate(instructions)
            if instruction.opcode == Opcode.BRANCH
        ),
        len(instructions),
    )
    best: tuple[int, list[int]] | None = None
    best_savings = 0
    for block_length in (64, 32, 16, 8, 4):
        groups: dict[tuple[tuple[tuple[int, ...], int], ...], list[int]] = defaultdict(list)
        for start in range(0, branch_idx - block_length + 1):
            block = instructions[start : start + block_length]
            if any(item.opcode != Opcode.CONTINUE for item in block):
                continue
            if (
                block[0].duration
                < profile.minimum_cycles_for(Opcode.JSR) * profile.clock_period_ns
                or block[-1].duration
                < profile.minimum_cycles_for(Opcode.RTS) * profile.clock_period_ns
            ):
                continue
            signature = tuple(_instruction_signature(item) for item in block)
            groups[signature].append(start)

        for starts in groups.values():
            selected: list[int] = []
            next_available = -1
            for start in starts:
                if start >= next_available:
                    selected.append(start)
                    next_available = start + block_length
            if len(selected) < 2:
                continue
            savings = (len(selected) - 1) * (block_length - 1)
            if savings > best_savings:
                best = block_length, selected
                best_savings = savings

    if best is None:
        return instructions

    block_length, starts = best
    starts_set = set(starts)
    source_start = starts[0]
    source_block = instructions[source_start : source_start + block_length]
    rewritten: list[Instruction] = []
    old_to_new: dict[int, int] = {}
    call_indices: list[int] = []
    old_idx = 0
    while old_idx < len(instructions):
        if old_idx in starts_set:
            old_to_new[old_idx] = len(rewritten)
            first = source_block[0]
            call_indices.append(len(rewritten))
            rewritten.append(replace(first, opcode=Opcode.JSR, inst_data=0))
            old_idx += block_length
            continue
        old_to_new[old_idx] = len(rewritten)
        instruction = instructions[old_idx]
        rewritten.append(
            replace(instruction, flags=instruction.flags.copy())
        )
        old_idx += 1

    for instruction in rewritten:
        if instruction.opcode in {Opcode.END_LOOP, Opcode.BRANCH, Opcode.JSR}:
            instruction.inst_data = old_to_new[instruction.inst_data]

    subroutine_address = len(rewritten)
    for call_idx in call_indices:
        rewritten[call_idx].inst_data = subroutine_address
    for block_idx, instruction in enumerate(source_block[1:], start=1):
        opcode = Opcode.RTS if block_idx == block_length - 1 else Opcode.CONTINUE
        rewritten.append(
            replace(
                instruction,
                flags=instruction.flags.copy(),
                opcode=opcode,
                inst_data=0,
            )
        )
    return rewritten


def _deduplicate_subroutines(
    instructions: list[Instruction], profile: BoardProfile
) -> list[Instruction]:
    current = instructions
    for _ in range(profile.max_subroutine_depth):
        optimized = _deduplicate_one_subroutine(current, profile)
        if optimized is current or len(optimized) >= len(current):
            break
        current = optimized
    return current


def _maximum_loop_depth(instructions: list[Instruction]) -> int:
    depth = 0
    maximum = 0
    for instruction in instructions:
        if instruction.opcode == Opcode.LOOP:
            depth += 1
            maximum = max(maximum, depth)
        elif instruction.opcode == Opcode.END_LOOP:
            depth -= 1
    return maximum


def _execution_count(
    instructions: list[Instruction], max_executed: int = 100_000_000
) -> int:
    """Count one pass through a program without materializing output arrays."""
    idx = 0
    executed = 0
    call_stack: list[int] = []
    loop_stack: list[tuple[int, int]] = []
    while 0 <= idx < len(instructions):
        executed += 1
        if executed > max_executed:
            raise ValueError(
                f"Program execution exceeds reporting limit {max_executed}"
            )
        instruction = instructions[idx]
        if instruction.opcode == Opcode.JSR:
            call_stack.append(idx + 1)
            idx = instruction.inst_data
        elif instruction.opcode == Opcode.RTS:
            idx = call_stack.pop()
        elif instruction.opcode == Opcode.LOOP:
            if not loop_stack or loop_stack[-1][0] != idx:
                loop_stack.append((idx, instruction.inst_data))
            idx += 1
        elif instruction.opcode == Opcode.END_LOOP:
            start, remaining = loop_stack[-1]
            remaining -= 1
            if remaining:
                loop_stack[-1] = start, remaining
                idx = start
            else:
                loop_stack.pop()
                idx += 1
        elif instruction.opcode in {Opcode.BRANCH, Opcode.STOP}:
            break
        else:
            idx += 1
    return executed


def _iter_executed_intervals(
    instructions: list[Instruction],
    profile: BoardProfile,
    *,
    max_executed: int = 100_000_000,
) -> Iterator[WaveformInterval]:
    """Yield one coalesced program pass without allocating an unrolled timeline."""
    idx = 0
    executed = 0
    call_stack: list[int] = []
    loop_stack: list[tuple[int, int]] = []
    pending: WaveformInterval | None = None
    while 0 <= idx < len(instructions):
        executed += 1
        if executed > max_executed:
            raise ValueError(
                f"Program execution exceeds verification limit {max_executed}"
            )

        instruction = instructions[idx]
        duration_ticks = instruction.duration // profile.clock_period_ns
        if instruction.opcode == Opcode.LONG_DELAY:
            duration_ticks *= instruction.inst_data
        current = WaveformInterval(tuple(instruction.flags), duration_ticks)
        if pending is not None and pending.flags == current.flags:
            pending = WaveformInterval(
                pending.flags, pending.duration_ticks + current.duration_ticks
            )
        else:
            if pending is not None:
                yield pending
            pending = current

        if instruction.opcode == Opcode.JSR:
            call_stack.append(idx + 1)
            idx = instruction.inst_data
        elif instruction.opcode == Opcode.RTS:
            idx = call_stack.pop()
        elif instruction.opcode == Opcode.LOOP:
            if not loop_stack or loop_stack[-1][0] != idx:
                loop_stack.append((idx, instruction.inst_data))
            idx += 1
        elif instruction.opcode == Opcode.END_LOOP:
            start, remaining = loop_stack[-1]
            remaining -= 1
            if remaining:
                loop_stack[-1] = start, remaining
                idx = start
            else:
                loop_stack.pop()
                idx += 1
        elif instruction.opcode in {Opcode.BRANCH, Opcode.STOP}:
            break
        else:
            idx += 1

    if pending is not None:
        yield pending


def _timeline_from_instructions(
    instructions: list[Instruction], profile: BoardProfile
) -> list[WaveformInterval]:
    return list(_iter_executed_intervals(instructions, profile))


def _validate_candidate_waveform(
    instructions: list[Instruction],
    reference: list[WaveformInterval],
    profile: BoardProfile,
) -> None:
    validate_sequence(instructions, profile=profile)
    actual = iter(_iter_executed_intervals(instructions, profile))
    for expected in reference:
        try:
            observed = next(actual)
        except StopIteration:
            raise ValueError(
                "Optimized program does not match the reference waveform"
            ) from None
        if observed != expected:
            raise ValueError("Optimized program does not match the reference waveform")
    if next(actual, None) is not None:
        raise ValueError("Optimized program does not match the reference waveform")


def _candidate_score(instructions: list[Instruction]) -> tuple[int, int, int, int]:
    return (
        len(instructions),
        _maximum_loop_depth(instructions),
        len({item.inst_data for item in instructions if item.opcode == Opcode.JSR}),
        sum(item.opcode != Opcode.CONTINUE for item in instructions),
    )


def _program_signature(
    instructions: list[Instruction],
) -> tuple[tuple[tuple[int, ...], int, int, int], ...]:
    return tuple(
        (
            tuple(instruction.flags),
            instruction.duration,
            int(instruction.opcode),
            instruction.inst_data,
        )
        for instruction in instructions
    )


def _select_compilation(
    intervals: list[WaveformInterval],
    profile: BoardProfile,
    level: OptimizationLevel,
) -> list[Instruction]:
    builders = {
        OptimizationLevel.NONE: [lambda: _compile_without_loops(intervals, profile)],
        OptimizationLevel.BASIC: [
            lambda: _compile_with_loops(intervals, profile, advanced=False)
        ],
        OptimizationLevel.ADVANCED: [
            # A raw program cannot fit when it already has more reference intervals
            # than instruction memory. Avoid allocating a predictably invalid copy.
            *(
                [lambda: _compile_without_loops(intervals, profile)]
                if len(intervals) <= profile.max_program_instructions
                else []
            ),
            lambda: _compile_with_loops(intervals, profile, advanced=False),
            lambda: _compile_with_loops(intervals, profile, advanced=True),
        ],
    }
    valid: list[list[Instruction]] = []
    errors: list[ValueError] = []
    seen: set[tuple[tuple[tuple[int, ...], int, int, int], ...]] = set()
    for build in builders[level]:
        candidate = build()
        candidates = [candidate]
        if level == OptimizationLevel.ADVANCED and profile.max_subroutine_depth > 0:
            with_subroutine = _deduplicate_subroutines(candidate, profile)
            if with_subroutine is not candidate:
                candidates.append(with_subroutine)
        for candidate_variant in candidates:
            signature = _program_signature(candidate_variant)
            if signature in seen:
                continue
            seen.add(signature)
            try:
                _validate_candidate_waveform(candidate_variant, intervals, profile)
            except ValueError as exc:
                errors.append(exc)
            else:
                valid.append(candidate_variant)
    if not valid:
        if errors and any("below minimum" in str(error) for error in errors):
            error = next(
                error for error in errors if "below minimum" in str(error)
            )
            raise ValueError(
                "Timing collision produced an interval shorter than the firmware "
                f"minimum: {error}"
            ) from error
        detail = min((str(error) for error in errors), default="no legal encoding")
        raise ValueError(f"Unable to compile waveform: {detail}")
    return min(valid, key=_candidate_score)


def minimum_duration_and_num_cycles(
    pulses: list[Signal],
    masking_pulses: list[Signal],
    duration: int | None,
    min_instruction_len: int,
    n_round: int = 5_000,
    n_digits: int = 4,
    t_max: int = int(10e9),
) -> tuple[int, int, list[float]]:
    """
    Calculate the minimum duration of the sequence of signals and the number of
    instruction cycles required

    Args:
        pulses (List[Signal]): signals composing the sequence
        masking_pulses (List[Signal]): masking signals
        duration (Optional[int]): duration of the sequence. None if not known.
        min_instruction_len (int): smallest possible instruction cycle [ns]
        n_round (int): round periods to the nearest n * minimum instruction lengths
        n_digits (int): round frequencies to the nearest n digits for checking if all
                        frequencies share a common divisor != 1
        t_max (int): maximum allowed duration of sequence [ns]

    Returns:
        Tuple[int, int, List[float]]:
            (duration [ns], number of instruction cycles, list of rescaled frequencies)
    """
    # nearest integer multiple # ns to round to
    ns_round = n_round * min_instruction_len
    # Get frequencies from signals
    frequencies = [p.frequency for p in pulses] + [
        p.frequency for p in masking_pulses
    ]
    if duration is None:
        gcd_freqs = gcd(*[int(round(f * 10**n_digits)) for f in frequencies])

        # if the gcd of all frequencies is equivalent to on of the frequencies
        # they have a common divisor and the period of the smallest frequency is
        # sufficient to fit all frequencies
        if gcd_freqs in [int(round(f * 10**n_digits)) for f in frequencies]:
            # round largest period to nearest integer multiple ns_round ns
            duration = max(
                min_instruction_len,
                round_to_nearest_n_ns(int(round(1 / min(frequencies) * 1e9)), ns_round),
            )
            fmin = min(frequencies)
            # convert the periods corresponding to the nearest
            # integer multiple ns_round to frequencies
            periods = [
                max(
                    min_instruction_len,
                    round_to_nearest_n_ns(
                        int(round(duration / (f / fmin))), min_instruction_len
                    ),
                )
                for f in frequencies
            ]
            frequencies = [1e9 / p for p in periods]
        else:
            #
            periods = [int(round(1 / f * 1e9)) for f in frequencies]
            if any(period < min_instruction_len for period in periods):
                raise ValueError(
                    "Signal period is shorter than the profile minimum instruction "
                    f"length ({min_instruction_len} ns)"
                )
            # clean up periods to nearest integer multiples of the minimum instruction
            # length
            periods = [p - p % min_instruction_len for p in periods]
            # Round long periods to the coarse grid used to keep the common
            # timespan manageable.  For periods shorter than that grid, use the
            # period itself as the quantum; otherwise a valid high-frequency
            # signal could be rounded all the way down to zero.
            periods = [
                round_to_nearest_n_ns(p, min(ns_round, p)) for p in periods
            ]

            # lowest common multiple of all periods
            lcm_periods = lcm(*periods)
            # if time exceeds t_max throw an exception, otherwise the sequence might
            # exceed the PulseBlaster memory capacity
            if lcm_periods >= t_max:
                raise ValueError(
                    "lcm timespan of input frequencies is too large,"
                    f" {lcm_periods*1e-9:.1e} s"
                )
            # total sequence duration in ns
            duration = lcm_periods
            # convert rounded periods to frequencies
            frequencies = [1e9 / p for p in periods]
    nr_cycles = int(duration / min_instruction_len)
    return duration, nr_cycles, frequencies


def pulses_convert_to_instruction_length(
    signals: list[Signal], min_instruction_len: int
) -> tuple[list[Pulse], list[int]]:
    """
    Convert the signals from durations of ns to durations in units of instruction
    lengths

    Args:
        pulses (List[Signal]): signals composing the sequence
        min_instruction_len (int): minimum instruction length [ns]

    Returns:
        Tuple[List[Pulse], List[int]]: tuple of signals in units of minimum instruction
                                        lengths and tuple of instruction cycles
    """
    pulses_cycle_units: list[Pulse] = []
    instruction_cycles: list[int] = []
    for signal in signals:
        f = int(round(1 / signal.frequency / (min_instruction_len * 1e-9)))
        o = int(round(signal.offset / min_instruction_len))
        if signal._duty_cycle_set:
            h = int(round(f * signal.duty_cycle))
        else:
            h = int(round(signal.high / min_instruction_len))
        if f <= 0:
            raise ValueError(
                "Signal period is shorter than the profile minimum instruction "
                f"length ({min_instruction_len} ns)"
            )
        if h <= 0:
            raise ValueError(
                "Signal high time is shorter than the profile minimum instruction "
                f"length ({min_instruction_len} ns)"
            )
        instruction_cycles.append(f)
        instruction_cycles.append(h)
        if o != 0:
            instruction_cycles.append(o)
        pulses_cycle_units.append(
            Pulse(
                period=f,
                channels=signal.channels,
                offset=o,
                high=h,
                active_high=signal.active_high,
            )
        )
    return pulses_cycle_units, instruction_cycles


def rescale_pulses(
    gcd_cycles: int,
    pulses_cycle_units: list[Pulse],
    masking_pulses_cycle_units: list[Pulse],
) -> tuple[list[Pulse], list[Pulse]]:
    """
    Rescale signals to the greatest common denominator of minimum instruction cycles
    required to generate the sequence

    Args:
        gcd_cycles (int): gcd in units of the minimum instruction length
        pulses_cycle_units (List[Pulse]): signals in units of the minimum instruction
                                            length
        masking_pulses_cycle_units (List[Pulse]): masking signals in units of the
                                                    minimum instruction length

    Returns:
        Tuple[List[Pulse], List[Pulse]]: tuple of a list of rescaled signals and
                                            masking signals
    """
    if gcd_cycles != 1:
        for pulse in pulses_cycle_units:
            pulse.period //= gcd_cycles
            pulse.offset //= gcd_cycles
            pulse.high //= gcd_cycles
        if masking_pulses_cycle_units is not None:
            for pulse in masking_pulses_cycle_units:
                pulse.period //= gcd_cycles
                pulse.offset //= gcd_cycles
                pulse.high //= gcd_cycles
    return pulses_cycle_units, masking_pulses_cycle_units


def calculate_resets(
    pulse_elapsed_cycles: list[int],
    pulses_cycle_units: list[Pulse],
    instruction_cycle: int,
) -> tuple[set[int], list[int]]:
    """
    Calculate if a given signal has elapsed a full period and needs to be reset.

    Args:
        pulse_elapsed_cycles (List[int]): elapsed time [minimum instruction cycles]
                                            since the start of each signal
        pulses_cycle_units (List[Pulse]): signals in units of minimum instruction cycle
        instruction_cycle (int): current instruction cycle

    Returns:
        Tuple[List[int], List[int]]: tuple containing a list of active channels and a
                                        a list of elapsed time per signal
                                        [minimum instruction cycles]
    """
    channels_active: set[int] = set()
    # cycle through the pulses
    for idx_pulse, pulse in enumerate(pulses_cycle_units):
        # check elapsed time is >= pulse offset
        if instruction_cycle >= pulse.offset:
            if pulse_elapsed_cycles[idx_pulse] < pulse.high:
                channels_active.update(pulse.channels)
            pulse_elapsed_cycles[idx_pulse] += 1
            pulse_elapsed_cycles[idx_pulse] = int(
                pulse_elapsed_cycles[idx_pulse] % pulse.period
            )
    return channels_active, pulse_elapsed_cycles


def calculate_resets_masking(
    masking_pulse_elapsed_cycles: list[int],
    base_channels: set[int] | list[Pulse],
    masking_pulses_cycle_units: list[Pulse],
    instruction_cycle: int,
) -> tuple[set[int], list[int]]:
    """
    Calculate if a given masking signal has elapsed a full period and needs to be reset.

    Args:
        masking_pulse_elapsed_cycles (List[int]): elapsed time [minimum instruction
                                                    cycles] since the start of each
                                                    masking signal
        base_channels (Set[int] | List[Pulse]): set of channels present in the
                                                base signal set or the pulse list
        masking_pulses_cycle_units (List[Pulse]): masking signals in units
                                                    [minimum instruction cycles]
        instruction_cycle (int): current instruction cycle

    Returns:
        Tuple[List[int], List[int]]: tuple containing a list of active channels and a
                                        list of elapsed time per signal
                                        [minimum instruction cycles]
    """
    if isinstance(base_channels, set):
        channels_masking_active = set(base_channels)
    else:
        channels_masking_active = {ch for pulse in base_channels for ch in pulse.channels}
    for idx_pulse, pulse in enumerate(masking_pulses_cycle_units):
        if instruction_cycle >= pulse.offset:
            if masking_pulse_elapsed_cycles[idx_pulse] >= pulse.high:
                channels_masking_active.difference_update(pulse.channels)
            masking_pulse_elapsed_cycles[idx_pulse] += 1
            masking_pulse_elapsed_cycles[idx_pulse] = int(
                masking_pulse_elapsed_cycles[idx_pulse] % pulse.period
            )

    return channels_masking_active, masking_pulse_elapsed_cycles


def generate_repeating_pulses(
    signals: list[Signal],
    masking_signals: list[Signal] | None = None,
    profile: BoardProfile = ESR_PRO_250,
    progress: bool = True,
    max_superperiod_ns: int = int(10e9),
    optimization: OptimizationLevel | str = OptimizationLevel.BASIC,
) -> InstructionSequence:
    """
    Generate repeating pulse signals for a PulseBlaster sequence.

    Args:
        signals (List[Signal]): signals in the sequence
        masking_signals (Optional[List[Signal]], optional): masking signals. Defaults to
                                                            None.
        profile (BoardProfile, optional): hardware profile used for timing, output
                                          width, and control-bit validation.
        progress (bool, optional): show progress bar. Defaults to True.
        max_superperiod_ns (int, optional): largest rational common timespan the
                                           compiler may generate. Defaults to 10 s.
        optimization (OptimizationLevel | str): compiler optimization policy.

    Returns:
        InstructionSequence: dataclass containing each instruction, time per instruction
                            and channel state for each instruction

    Raises:
        ValueError: if signals list is empty or invalid parameters provided
    """
    if not signals:
        raise ValueError("At least one signal must be provided")
    try:
        optimization_level = OptimizationLevel(optimization)
    except ValueError as exc:
        raise ValueError(f"Unknown optimization level {optimization!r}") from exc
    if masking_signals is None:
        masking_signals = []

    signal_channels = {ch for signal in signals for ch in signal.channels}
    masking_channels = {ch for signal in masking_signals for ch in signal.channels}
    max_controllable_channel = profile.output_bits - 1
    highest_requested_channel = max(signal_channels | masking_channels, default=-1)
    if highest_requested_channel > max_controllable_channel:
        raise ValueError(
            "Signal channels exceed the configured controllable range "
            f"0..{max_controllable_channel}. Got channel {highest_requested_channel}."
        )
    if not masking_channels.issubset(signal_channels):
        invalid_channels = sorted(masking_channels.difference(signal_channels))
        raise ValueError(
            f"Masking channels must be a subset of signal channels. "
            f"Invalid masking channels: {invalid_channels}"
        )

    compile_start = perf_counter()
    intervals, duration_ticks = _reference_intervals(
        signals, masking_signals, profile, max_superperiod_ns
    )
    pulse_sequence = _select_compilation(
        intervals, profile, optimization_level
    )
    compile_seconds = perf_counter() - compile_start
    executed_instructions = _execution_count(pulse_sequence)
    loop_count = sum(
        instruction.opcode == Opcode.LOOP for instruction in pulse_sequence
    )
    subroutine_targets = {
        instruction.inst_data
        for instruction in pulse_sequence
        if instruction.opcode == Opcode.JSR
    }
    report = CompilationReport(
        optimization_level=optimization_level,
        superperiod_ns=duration_ticks * profile.clock_period_ns,
        reference_intervals=len(intervals),
        stored_instructions=len(pulse_sequence),
        executed_instructions=executed_instructions,
        loop_count=loop_count,
        maximum_loop_depth=_maximum_loop_depth(pulse_sequence),
        long_delay_count=sum(
            instruction.opcode == Opcode.LONG_DELAY
            for instruction in pulse_sequence
        ),
        subroutine_count=len(subroutine_targets),
        maximum_subroutine_depth=_maximum_subroutine_depth(pulse_sequence),
        compression_ratio=executed_instructions / len(pulse_sequence),
        compile_seconds=compile_seconds,
    )

    logging.info("superperiod_ticks = %d", duration_ticks)
    logging.info("reference_intervals = %d", len(intervals))
    logging.info("compressed_instructions = %d", len(pulse_sequence))
    return InstructionSequence(pulse_sequence, compilation_report=report)
