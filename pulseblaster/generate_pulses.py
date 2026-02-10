"""
Pulse sequence generation for PulseBlaster devices.

This module provides functions for generating repeating pulse sequences
with multiple frequencies and converting them to PulseBlaster instructions.
"""

import logging
from copy import deepcopy
from math import gcd, lcm
from typing import List, Optional, Set, Tuple, Union

import tqdm

from .data_structures import Instruction, InstructionSequence, Opcode, Pulse, Signal
from .utils import all_channels_off, round_to_nearest_n_ns, set_reserved_channels


def minimum_duration_and_num_cycles(
    pulses: List[Signal],
    masking_pulses: List[Signal],
    duration: Optional[int],
    min_instruction_len: int,
    n_round: int = 5_000,
    n_digits: int = 4,
    t_max: int = int(10e9),
) -> Tuple[int, int, List[float]]:
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
            duration = round_to_nearest_n_ns(
                int(round(1 / min(frequencies) * 1e9)), ns_round
            )
            fmin = min(frequencies)
            # convert the periods corresponding to the nearest
            # integer multiple ns_round to frequencies
            frequencies = [
                1e9
                / round_to_nearest_n_ns(
                    int(round(duration / (f / fmin))), min_instruction_len
                )
                for f in frequencies
            ]
        else:
            #
            periods = [int(round(1 / f * 1e9)) for f in frequencies]
            # clean up periods to nearest integer multiples of the minimum instruction
            # length
            periods = [p - p % min_instruction_len for p in periods]
            # round periods to nearest integer multiple ns_round ns
            periods = [round_to_nearest_n_ns(p, ns_round) for p in periods]

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
    signals: List[Signal], min_instruction_len: int
) -> Tuple[List[Pulse], List[int]]:
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
    pulses_cycle_units: List[Pulse] = []
    instruction_cycles: List[int] = []
    for signal in signals:
        f = int(round(1 / signal.frequency / (min_instruction_len * 1e-9)))
        o = int(round(signal.offset / min_instruction_len))
        if signal._duty_cycle_set:
            h = int(round(f * signal.duty_cycle))
        else:
            h = int(round(signal.high / min_instruction_len))
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
    pulses_cycle_units: List[Pulse],
    masking_pulses_cycle_units: List[Pulse],
) -> Tuple[List[Pulse], List[Pulse]]:
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
    pulse_elapsed_cycles: List[int],
    pulses_cycle_units: List[Pulse],
    instruction_cycle: int,
) -> Tuple[Set[int], List[int]]:
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
    channels_active: Set[int] = set()
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
    masking_pulse_elapsed_cycles: List[int],
    base_channels: Union[Set[int], List[Pulse]],
    masking_pulses_cycle_units: List[Pulse],
    instruction_cycle: int,
) -> Tuple[Set[int], List[int]]:
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
    signals: List[Signal],
    masking_signals: Optional[List[Signal]] = None,
    min_instruction_len: int = 20,
    nr_channels: int = 24,
    reserved_channels: int = 3,
    progress: bool = True,
) -> InstructionSequence:
    """
    Generate repeating pulse signals for a PulseBlaster sequence.

    Args:
        signals (List[Signal]): signals in the sequence
        masking_signals (Optional[List[Signal]], optional): masking signals. Defaults to
                                                            None.
        min_instruction_len (int, optional): minimum instruction length [ns]. Defaults
                                            to 20 ns.
        nr_channels (int, optional): number of channels. Defaults to 24.
        reserved_channels (int, optional): number of trailing channels that mirror
                                            overall channel state. Defaults to 3.
        progress (bool, optional): show progress bar. Defaults to True.

    Returns:
        InstructionSequence: dataclass containing each instruction, time per instruction
                            and channel state for each instruction

    Raises:
        ValueError: if signals list is empty or invalid parameters provided
    """
    # Input validation
    if not signals:
        raise ValueError("At least one signal must be provided")
    if min_instruction_len <= 0:
        raise ValueError(f"min_instruction_len must be positive, got {min_instruction_len}")
    if nr_channels <= 0:
        raise ValueError(f"nr_channels must be positive, got {nr_channels}")
    if reserved_channels < 0:
        raise ValueError(f"reserved_channels must be non-negative, got {reserved_channels}")
    if reserved_channels >= nr_channels:
        raise ValueError(
            f"reserved_channels ({reserved_channels}) must be less than nr_channels ({nr_channels})"
        )

    c: List[List[int]] = []
    t: List[int] = []

    if masking_signals is None:
        masking_signals = []

    signal_channels = {ch for signal in signals for ch in signal.channels}
    masking_channels = {ch for signal in masking_signals for ch in signal.channels}
    max_controllable_channel = nr_channels - reserved_channels - 1
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

    # calculating the minimum duration required to perform all pulses
    duration, nr_cycles, frequencies_rescaled = minimum_duration_and_num_cycles(
        signals, masking_signals, None, min_instruction_len
    )

    # copy pulses, otherwise below will overwrite inputs (which are by reference)
    signals = deepcopy(signals)
    masking_signals = deepcopy(masking_signals)

    # set the rescaled frequencies
    for signal, freq_rescaled in zip(signals, frequencies_rescaled):
        signal.frequency = freq_rescaled
    for masking_signal, freq_rescaled in zip(
        masking_signals, frequencies_rescaled[-len(masking_signals) :]
    ):
        masking_signal.frequency = freq_rescaled

    # calculating the offset, frequency and amount of time at high state
    # in units of minimum_instruction_len
    pulses_cycle_units, instruction_cycles = pulses_convert_to_instruction_length(
        signals, min_instruction_len
    )

    # calculating the offset, frequency of the masking pulses
    (
        masking_pulses_cycle_units,
        instruction_cycles_masking,
    ) = pulses_convert_to_instruction_length(masking_signals, min_instruction_len)
    instruction_cycles.extend(instruction_cycles_masking)

    # checking whether the minimum clock cycles instruction length can be increased
    # by checking for the greatest common denominator to speed up sequence generation
    if instruction_cycles:
        instruction_cycles.append(nr_cycles)

    # calculate the greatest command denominator
    gcd_cycles = gcd(*instruction_cycles)

    # rescale the signals to the new minimum instruction length
    pulses_cycle_units, masking_pulses_cycle_units = rescale_pulses(
        gcd_cycles, pulses_cycle_units, masking_pulses_cycle_units
    )

    # rescaling the minimum instruction length by the greatest common denominator
    min_instruction_len *= gcd_cycles
    nr_cycles = int(duration / min_instruction_len) - 1

    logging.info(f"gcd_cycles = {gcd_cycles}")
    logging.info(f"min_instruction_len rescaled = {min_instruction_len}")
    logging.info(f"nr_cycles = {nr_cycles}")

    # generating the pulseblaster instructions
    pulse_elapsed_cycles = [0] * len(signals)
    masking_pulse_elapsed_cycles = [0] * len(masking_signals)
    active_low_channels = {
        ch for signal in signals if not signal.active_high for ch in signal.channels
    }

    for instruction_cycle in tqdm.tqdm(
        range(nr_cycles), disable=not progress, desc="Instruction cycles"
    ):
        # calculating the elapsed time [minimum instruction cycles] since the start of
        # each signal, resets when a full period has elapsed (i.e. channels_active for
        # a channel changes from high to low)
        channels_active, pulse_elapsed_cycles = calculate_resets(
            pulse_elapsed_cycles, pulses_cycle_units, instruction_cycle
        )

        # calculating the elapsed time [minimum instruction cycles] since the start of
        # each masking signal, resets when a full period has elapsed (i.e.
        # channels_active for a channel changes from high to low)
        (
            channels_masking_active,
            masking_pulse_elapsed_cycles,
        ) = calculate_resets_masking(
            masking_pulse_elapsed_cycles,
            signal_channels,
            masking_pulses_cycle_units,
            instruction_cycle,
        )

        gated_channels = channels_active.intersection(channels_masking_active)
        chs = [0] * nr_channels
        for channel in gated_channels:
            chs[channel] = 1

        # invert active state for channels that are configured active low
        for channel in active_low_channels:
            chs[channel] = 0 if chs[channel] else 1

        set_reserved_channels(chs, reserved_channels)

        if not c:
            t.append(min_instruction_len)
            c.append(chs)
        elif c[-1] == chs:
            t[-1] += min_instruction_len
        else:
            t.append(min_instruction_len)
            c.append(chs)

    # set all channels low for the last instruction, which will branch back to the first
    # instruction so as to repeat the pulse sequence
    channels_off = all_channels_off(
        signals, nr_channels=nr_channels, reserved_channels=reserved_channels
    )

    # Convert the time and channel state lists into Instructions
    pulse_sequence: List[Instruction] = []
    for t_, c_ in zip(t, c):
        pulse_sequence.append(
            Instruction(
                label="", flags=c_, duration=t_, opcode=Opcode.CONTINUE, inst_data=0
            )
        )
    pulse_sequence.append(
        Instruction(
            label="",
            flags=channels_off.copy(),
            duration=min_instruction_len,
            opcode=Opcode.BRANCH,
            inst_data=0,
        )
    )

    return InstructionSequence(pulse_sequence)
