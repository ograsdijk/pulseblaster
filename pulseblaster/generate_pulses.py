"""
Pulse sequence generation for PulseBlaster devices.

This module provides functions for generating repeating pulse sequences
with multiple frequencies and converting them to PulseBlaster instructions.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass
from fractions import Fraction
from math import gcd, lcm

from .data_structures import Instruction, InstructionSequence, Opcode, Pulse, Signal
from .utils import round_to_nearest_n_ns
from .validation import ESR_PRO_250, BoardProfile, set_control_mode, validate_sequence


@dataclass(frozen=True)
class WaveformInterval:
    """One constant-output interval expressed in hardware clock ticks."""

    flags: tuple[int, ...]
    duration_ticks: int


def _round_fraction(value: Fraction) -> int:
    """Round a non-negative fraction to the nearest integer, halves upward."""
    if value < 0:
        raise ValueError(f"Cannot round negative timing value {value}")
    return (2 * value.numerator + value.denominator) // (2 * value.denominator)


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

    events: list[tuple[int, bool]] = []
    occurrence = 0
    while True:
        rise_exact = offset_ticks + occurrence * period_ticks
        if rise_exact >= duration_ticks:
            break
        rise = _round_fraction(rise_exact)
        fall = _round_fraction(rise_exact + high_ticks)
        if fall <= rise:
            raise ValueError(
                f"Signal on channels {signal.channels} has a pulse shorter than one "
                f"{tick_ns} ns clock tick"
            )
        if rise < duration_ticks:
            events.append((rise, True))
        if fall < duration_ticks:
            events.append((fall, False))
        occurrence += 1
    return events


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
    events: dict[int, list[tuple[bool, int, bool]]] = defaultdict(list)

    for idx, signal in enumerate(signals):
        for event_tick, active in _signal_event_times(
            signal, duration_ticks, tick_ns
        ):
            events[event_tick].append((False, idx, active))
    for idx, signal in enumerate(masking_signals):
        for event_tick, active in _signal_event_times(
            signal, duration_ticks, tick_ns
        ):
            events[event_tick].append((True, idx, active))

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

    boundaries = sorted({0, duration_ticks, *events})
    intervals: list[WaveformInterval] = []
    for boundary_idx, tick in enumerate(boundaries[:-1]):
        for is_mask, signal_idx, active in events.get(tick, []):
            if is_mask:
                active_masks[signal_idx] = active
            else:
                active_signals[signal_idx] = active

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

        duration = boundaries[boundary_idx + 1] - tick
        interval = WaveformInterval(tuple(flags), duration)
        if intervals and intervals[-1].flags == interval.flags:
            previous_interval = intervals[-1]
            intervals[-1] = WaveformInterval(
                previous_interval.flags, previous_interval.duration_ticks + duration
            )
        else:
            intervals.append(interval)

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
    for pattern_length in range(2, maximum + 1):
        repetitions = _repeat_count(intervals, start, pattern_length, stop)
        if repetitions < 2:
            continue
        pattern = intervals[start : start + pattern_length]
        if pattern[0].duration_ticks < profile.minimum_cycles_for(Opcode.LOOP):
            continue
        if pattern[-1].duration_ticks < profile.minimum_cycles_for(Opcode.END_LOOP):
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


def _compress_intervals(
    intervals: list[WaveformInterval], profile: BoardProfile
) -> list[Instruction]:
    """Compress adjacent repeated waveform blocks with hardware loops."""
    if not intervals:
        raise ValueError("Waveform contains no intervals")

    instructions: list[Instruction] = []
    # Reserve the last real interval for BRANCH so the branch adds no time.
    stop = len(intervals) - 1
    idx = 0
    while idx < stop:
        repeat = _best_repeat(intervals, idx, stop, profile)
        if repeat is None:
            instructions.append(_instruction(intervals[idx], profile))
            idx += 1
            continue

        pattern_length, repetitions = repeat
        pattern = intervals[idx : idx + pattern_length]
        while repetitions:
            chunk = min(repetitions, profile.max_loop_iterations)
            if chunk == 1:
                instructions.extend(_instruction(item, profile) for item in pattern)
            else:
                loop_address = len(instructions)
                for pattern_idx, item in enumerate(pattern):
                    if pattern_idx == 0:
                        instructions.append(
                            _instruction(item, profile, Opcode.LOOP, chunk)
                        )
                    elif pattern_idx == pattern_length - 1:
                        instructions.append(
                            _instruction(
                                item, profile, Opcode.END_LOOP, loop_address
                            )
                        )
                    else:
                        instructions.append(_instruction(item, profile))
            repetitions -= chunk
        idx += repeat[0] * repeat[1]

    instructions.append(_instruction(intervals[-1], profile, Opcode.BRANCH, 0))
    return instructions


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

    Returns:
        InstructionSequence: dataclass containing each instruction, time per instruction
                            and channel state for each instruction

    Raises:
        ValueError: if signals list is empty or invalid parameters provided
    """
    if not signals:
        raise ValueError("At least one signal must be provided")
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

    intervals, duration_ticks = _reference_intervals(
        signals, masking_signals, profile, max_superperiod_ns
    )
    pulse_sequence = _compress_intervals(intervals, profile)
    try:
        validate_sequence(pulse_sequence, profile=profile)
    except ValueError as exc:
        if "below minimum" in str(exc):
            raise ValueError(
                "Timing collision produced an interval shorter than the firmware "
                f"minimum: {exc}"
            ) from exc
        raise

    logging.info("superperiod_ticks = %d", duration_ticks)
    logging.info("reference_intervals = %d", len(intervals))
    logging.info("compressed_instructions = %d", len(pulse_sequence))
    return InstructionSequence(pulse_sequence)
