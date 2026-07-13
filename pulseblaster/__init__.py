"""
PulseBlaster package for generating and controlling pulse sequences.

This package provides tools for:
- Generating repeating pulse sequences with multiple frequencies
- Programming SpinCore PulseBlaster boards
- Visualizing pulse sequences
- Converting assembly code to instructions
"""

from . import generate_pulses
from .data_structures import (
    CompilationReport,
    Instruction,
    InstructionSequence,
    OptimizationLevel,
    Signal,
)
from .device import PulseBlaster
from .plot_utils import plot_sequence
from .read_code import code_to_instructions
from .utils import number_of_boards_connected
from .validation import ESR_PRO_250, BoardProfile, validate_sequence

__all__ = [
    "generate_pulses",
    "Signal",
    "Instruction",
    "InstructionSequence",
    "OptimizationLevel",
    "CompilationReport",
    "PulseBlaster",
    "plot_sequence",
    "code_to_instructions",
    "number_of_boards_connected",
    "BoardProfile",
    "ESR_PRO_250",
    "validate_sequence",
]
