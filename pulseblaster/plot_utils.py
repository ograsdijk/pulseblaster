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
) -> tuple[Figure, Axes]:
    """
    Plot a PulseBlaster instruction sequence.

    Args:
        sequence (InstructionSequence): instruction sequence to plot
        ax (Optional[Axes]): matplotlib axes to plot on. If None, creates new figure
        offset (float): vertical offset between channels
        fontsize (int): font size for labels and ticks
        div (Optional[float]): time unit divisor (1e9 for s, 1e6 for ms, etc.).
                               If None, automatically selects appropriate unit

    Returns:
        tuple[Figure, Axes]: matplotlib figure and axes objects
    """
    if ax is None:
        fig, ax = plt.subplots(figsize=(8, 5))
    else:
        fig = None

    c = sequence.flags.copy()

    select = np.where(np.sum(c[:, :21], axis=0) != 0)[0]

    c = c[:, select]
    c = np.append(np.zeros((1, len(select))), c, axis=0)

    # get time units
    # convert to 64 bits because 32 bit signed integers overflow when the # ns exceeds
    # ~2.14 s, 64 bit integers overflow for ~292 years worth of ns
    t = np.append([0], sequence.duration).astype(np.int64)
    time_units = {1e9: "s", 1e6: "ms", 1e3: "μs", 1: "ns"}
    tmax = sum(t)
    if div is None:
        for _div in [1e9, 1e6, 1e3, 1]:
            if tmax / _div >= 1:
                break
    else:
        _div = div

    for idc, c in enumerate(c.T):
        ax.step(
            np.cumsum(t.astype(np.int64)) / _div, c + offset * idc, lw=3, where="pre"
        )

    if sequence.branch_index is not None:
        ax.axvline(
            np.cumsum(t.astype(np.int64))[sequence.branch_index] / _div,
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

    ax.legend(fontsize=fontsize)

    return fig, ax
