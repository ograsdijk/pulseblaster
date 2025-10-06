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


def all_channels_off(pulses: List[Signal]) -> List[int]:
    """
    Generate the instruction for all channels off

    Args:
        pulses (List[Signal]): signals composing sequence

    Returns:
        List[int]: channel state
    """
    c = [0] * 24
    for pulse in pulses:
        if not pulse.active_high:
            for ch in pulse.channels:
                c[ch] = 1
            # last 3 'channels' are not separately controllable, see manual
            # need to be high
            c[-3:] = [1, 1, 1]
    return c


def number_of_boards_connected() -> int:
    """
    Get the number of PulseBlaster boards connected to the computer

    Returns:
        int: number of connected boards
    """
    return pb_count_boards()
