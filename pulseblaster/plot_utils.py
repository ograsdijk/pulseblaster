"""
Plotting utilities for visualizing PulseBlaster pulse sequences.

This module provides functions for plotting instruction sequences
to visualize the timing and state of different channels.
"""

from typing import Optional

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.axes import Axes
from matplotlib.figure import Figure

from .data_structures import InstructionSequence


def plot_sequence(
    sequence: InstructionSequence,
    ax: Optional[Axes] = None,
    offset: float = 1.3,
    fontsize: int = 14,
    div: Optional[float] = None,
    exclude_channels: Optional[list[int]] = None,
) -> tuple[Optional[Figure], Axes]:
    """
    Plot a PulseBlaster instruction sequence.

    Args:
        sequence (InstructionSequence): instruction sequence to plot
        ax (Optional[Axes]): matplotlib axes to plot on. If None, creates new figure
        offset (float): vertical offset between channels
        fontsize (int): font size for labels and ticks
        div (Optional[float]): time unit divisor (1e9 for s, 1e6 for ms, etc.).
                               If None, automatically selects appropriate unit
        exclude_channels (Optional[list[int]]): channels to hide from the plot

    Returns:
        tuple[Optional[Figure], Axes]: matplotlib figure and axes objects
    """
    if ax is None:
        fig, ax = plt.subplots(figsize=(8, 5))
    else:
        fig = None

    c = sequence.flags.copy()

    select = np.where(np.sum(c, axis=0) != 0)[0]
    if exclude_channels:
        exclude_set = set(exclude_channels)
        nr_channels = c.shape[1]
        invalid_channels = sorted(
            ch for ch in exclude_set if ch < 0 or ch >= nr_channels
        )
        if invalid_channels:
            raise ValueError(
                f"exclude_channels must be in range 0..{nr_channels - 1}, "
                f"got {invalid_channels}"
            )
        select = np.asarray([ch for ch in select if ch not in exclude_set], dtype=int)

    c = c[:, select]
    c = np.append(np.zeros((1, len(select))), c, axis=0)

    # convert to 64 bits because 32-bit signed integers overflow when the # ns exceeds
    # ~2.14 s, while 64-bit integers overflow for ~292 years worth of ns.
    t = np.append([0], sequence.duration).astype(np.int64)
    time_units = {1e9: "s", 1e6: "ms", 1e3: "us", 1: "ns"}
    tmax = int(np.sum(t))
    if div is None:
        for _div in [1e9, 1e6, 1e3, 1]:
            if tmax / _div >= 1:
                break
    else:
        _div = div

    cumulative_t = np.cumsum(t.astype(np.int64))
    for idc, channel_values in enumerate(c.T):
        ax.step(cumulative_t / _div, channel_values + offset * idc, lw=3, where="pre")

    if sequence.branch_index is not None:
        ax.axvline(
            cumulative_t[sequence.branch_index] / _div,
            lw=3,
            label="branch",
            color="k",
            linestyle="--",
        )

    ax.set_yticks([0.5 + offset * idc for idc in range(len(select))])
    ax.set_yticklabels([f"CH{ch}" for ch in select], fontsize=fontsize)

    ax.set_xlabel(f"time [{time_units[_div]}]", fontsize=fontsize)

    ax.tick_params(axis="both", which="major", labelsize=fontsize)
    ax.tick_params(axis="both", which="minor", labelsize=fontsize * 0.8)
    ax.xaxis.get_offset_text().set_size(fontsize)

    if sequence.branch_index is not None:
        ax.legend(fontsize=fontsize)

    return fig, ax
