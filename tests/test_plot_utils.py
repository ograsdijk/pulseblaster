"""Tests for plot_utils module."""

import matplotlib
import pytest

matplotlib.use("Agg")

from pulseblaster.data_structures import Instruction, InstructionSequence, Opcode
from pulseblaster.plot_utils import plot_sequence


def _sequence_with_channels(*channels: int) -> InstructionSequence:
    flags = [0] * 24
    for ch in channels:
        flags[ch] = 1
    instructions = [
        Instruction("", flags.copy(), 100, Opcode.CONTINUE, 0),
        Instruction("", [0] * 24, 100, Opcode.STOP, 0),
    ]
    return InstructionSequence(instructions)


class TestPlotSequence:
    """Tests for plot_sequence function."""

    def test_exclude_channels_removes_from_y_axis_labels(self):
        """Excluded channels should not be shown in y-axis labels."""
        sequence = _sequence_with_channels(0, 7, 21, 22, 23)

        fig, ax = plot_sequence(sequence, exclude_channels=[21, 22, 23])
        labels = [tick.get_text() for tick in ax.get_yticklabels()]

        assert "CH0" in labels
        assert "CH7" in labels
        assert "CH21" not in labels
        assert "CH22" not in labels
        assert "CH23" not in labels
        if fig is not None:
            fig.clf()

    def test_exclude_channels_validation(self):
        """Invalid excluded channels should raise ValueError."""
        sequence = _sequence_with_channels(0)

        with pytest.raises(ValueError, match="exclude_channels must be in range"):
            plot_sequence(sequence, exclude_channels=[24])
