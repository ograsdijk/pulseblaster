"""
Utility functions for PulseBlaster pulse sequence generation.

This module provides helper functions for rounding, channel management,
and hardware detection.
"""

from typing import List

from spinapi import pb_count_boards

from .data_structures import Signal


def round_to_nearest_n_ns(value: int, ns_round: int) -> int:
    """
    Round a value to the nearest multiple of ns_round.

    Args:
        value (int): value to round [ns]
        ns_round (int): round to nearest multiple of this value [ns]

    Returns:
        int: rounded value [ns]
    """
    return int(round(value / ns_round) * ns_round)


def set_reserved_channels(flags: List[int], reserved_channels: int) -> None:
    """
    Set reserved trailing channels to reflect whether any non-reserved channel is high.

    Args:
        flags (List[int]): channel-state flags, modified in-place
        reserved_channels (int): number of trailing reserved channels
    """
    if reserved_channels <= 0:
        return
    if reserved_channels >= len(flags):
        raise ValueError(
            f"reserved_channels ({reserved_channels}) must be less than total channels ({len(flags)})"
        )

    reserved_value = 1 if any(flags[:-reserved_channels]) else 0
    flags[-reserved_channels:] = [reserved_value] * reserved_channels


def all_channels_off(
    pulses: List[Signal],
    nr_channels: int = 24,
    reserved_channels: int = 3,
) -> List[int]:
    """
    Generate the instruction for all channels off.

    Args:
        pulses (List[Signal]): signals composing sequence
        nr_channels (int): total number of channels represented in flags
        reserved_channels (int): trailing channels that mirror overall state

    Returns:
        List[int]: channel state
    """
    c = [0] * nr_channels
    for pulse in pulses:
        if not pulse.active_high:
            for ch in pulse.channels:
                c[ch] = 1
    set_reserved_channels(c, reserved_channels)
    return c


def number_of_boards_connected() -> int:
    """
    Get the number of PulseBlaster boards connected to the computer

    Returns:
        int: number of connected boards
    """
    return pb_count_boards()
