"""
PulseBlaster package for generating and controlling pulse sequences.

This package provides tools for:
- Generating repeating pulse sequences with multiple frequencies
- Programming SpinCore PulseBlaster boards
- Visualizing pulse sequences
- Converting assembly code to instructions
"""

from . import generate_pulses, profiles, program_cache
from ._spinapi_runtime import configure_spinapi_runtime, use_spinapi_dll
from .data_structures import (
    CompilationReport,
    Instruction,
    InstructionSequence,
    OptimizationLevel,
    Signal,
)
from .device import PulseBlaster, PulseBlasterStatus
from .plot_utils import plot_sequence
from .read_code import code_to_instructions
from .utils import number_of_boards_connected
from .validation import (
    BOARD_PROFILES,
    ESR_PRO_250,
    BoardProfile,
    get_board_profile,
    validate_sequence,
)

# Opt-in only: apply PULSEBLASTER_SPINAPI_DLL if set. Without it the upstream
# spinapi wrapper loads the installed spinapi64.dll as usual on its first call.
configure_spinapi_runtime()

__all__ = [
    "generate_pulses",
    "profiles",
    "program_cache",
    "Signal",
    "Instruction",
    "InstructionSequence",
    "OptimizationLevel",
    "CompilationReport",
    "PulseBlaster",
    "use_spinapi_dll",
    "PulseBlasterStatus",
    "plot_sequence",
    "code_to_instructions",
    "number_of_boards_connected",
    "BoardProfile",
    "BOARD_PROFILES",
    "ESR_PRO_250",
    "get_board_profile",
    "validate_sequence",
]
