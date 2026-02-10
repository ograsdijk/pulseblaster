"""
Data structures for PulseBlaster pulse sequence generation.

This module defines the core data structures used for representing pulses,
signals, instructions, and sequences for the PulseBlaster hardware.
"""

from dataclasses import dataclass, field
from enum import IntEnum
from typing import List, Optional, Tuple

import numpy as np
import numpy.typing as npt
from spinapi import BRANCH, CONTINUE, END_LOOP, JSR, LONG_DELAY, LOOP, RTS, STOP, WAIT


@dataclass
class Pulse:
    """Period dataclass for generate pulse sequences with a PulseBlaster

    Args:
        period (int): period [ns]
        channels (list[int]): list with output channels
        offset (int): signal offset from trigger [ns]
        high (int): signal high time [ns]
        active_high (bool): signal active high or active low
    """

    period: int
    channels: List[int]
    offset: int
    high: int
    active_high: bool


@dataclass
class Signal:
    """Signal dataclass for generating pulses sequences with a PulseBlaster

    If neither `high` nor `duty_cycle` are specified, a duty cycle of 50% is assumed.

    Args:
        frequency (int): frequency [Hz]
        channels (list[int]): list with output channels
        offset (int): signal offset from trigger [ns]
        high (int): signal high time [ns]
        active_high (bool): signal active high or active low
        duty_cycle (float): duty cycle of signal, between 0 and 1
    """

    frequency: float
    channels: List[int]
    offset: int = 0
    duty_cycle: float = 0.5
    high: int = 0
    active_high: bool = True

    def __post_init__(self):
        # Validate inputs
        if self.frequency <= 0:
            raise ValueError(f"Frequency must be positive, got {self.frequency}")
        if self.offset < 0:
            raise ValueError(f"Offset must be non-negative, got {self.offset}")
        if self.high < 0:
            raise ValueError(f"High time must be non-negative, got {self.high}")
        if not 0 <= self.duty_cycle <= 1:
            raise ValueError(f"Duty cycle must be between 0 and 1, got {self.duty_cycle}")
        if not self.channels:
            raise ValueError("At least one channel must be specified")
        if any(ch < 0 for ch in self.channels):
            raise ValueError(f"Channels must be non-negative, got {self.channels}")

        if self.high == 0:
            self.high = int((1 / self.frequency * 1e9) * self.duty_cycle)
            self._duty_cycle_set = True
        else:
            self.duty_cycle = self.high / (1 / self.frequency * 1e9)
            self._duty_cycle_set = False

        if 1 / self.frequency <= (self.high * 1e-9):
            raise ValueError(
                f"Pulse high {self.high:.0e} >= period {1/self.frequency * 1e9:.0e}"
            )


class Opcode(IntEnum):
    """
    CONTINUE
    STOP        stop execution
    LOOP        start loop, inst_data = # iterations
    END_LOOP    stop loop
    JSR         enter subroutine, inst_data = subroutine address
    RTS         end of subroutine
    BRANCH      branch to address, inst_data = branch address
    LONG_DELAY
    WAIT        wait for trigger
    """

    CONTINUE = CONTINUE
    STOP = STOP
    LOOP = LOOP
    END_LOOP = END_LOOP
    JSR = JSR
    RTS = RTS
    BRANCH = BRANCH
    LONG_DELAY = LONG_DELAY
    WAIT = WAIT


@dataclass
class Instruction:
    label: str
    flags: List[int]
    duration: int
    opcode: Opcode
    inst_data: int = 0


@dataclass
class Loop:
    idx_start: int
    iterations_left: int


def unroll_duration_flags(
    instructions: List[Instruction],
) -> Tuple[npt.NDArray[np.int_], npt.NDArray[np.int_], Optional[int]]:
    """
    generate the unrolled durations and flags that compose the entire instruction set,
    useful for plotting or inspecting the total pulse sequence.
    Here unrolled refers to unrolling the loops and subroutines

    Args:
        instructions (List[Instruction]): set of instructions

    Returns:
        tuple[npt.NDArray[np.int_], npt.NDArray[np.int_], Optional[int]]:
            duration of each instruction, flag of each instruction and the index where
            the branch returns to if a BRANCH opcode is present.
    """
    if not instructions:
        raise ValueError("Instruction list cannot be empty")

    _duration: list[int] = []
    _flags: list[list[int]] = []
    _addresses: list[int] = []
    branch_index: Optional[int] = None

    idx = 0
    # subroutines contains the indices of the JSR opcodes
    subroutines: list[int] = []
    # loops contains the Loop dataclass, which contains the idx_start and
    # iterations_left of the loop
    loops: list[Loop] = []
    while 0 <= idx < len(instructions):
        # load instruction
        instruction = instructions[idx]

        # append the duration and flag bytes
        _duration.append(instruction.duration)
        _flags.append(instruction.flags)
        _addresses.append(idx)

        # instructions with the following opcodes require some extra work
        if instruction.opcode == Opcode.JSR:
            # JSR opcode is a jump to a subroutine at the index indicated in the opcode
            # until the RTS opcode is reached, when it will return to the JSR index + 1
            if not 0 <= instruction.inst_data < len(instructions):
                raise ValueError(
                    f"JSR target {instruction.inst_data} is outside instruction range"
                )
            subroutines.append(idx)
            idx = instruction.inst_data
        elif instruction.opcode == Opcode.RTS:
            # once RTS has been reached, grab the index and add 1 to proceed to
            # the next instruction
            if not subroutines:
                raise ValueError("RTS encountered without a matching JSR")
            idx = subroutines.pop() + 1
        elif instruction.opcode == Opcode.LOOP:
            # LOOP opcode loops through the instructions up to END_LOOP by ins_data
            # times
            if instruction.inst_data <= 0:
                raise ValueError(
                    f"LOOP iterations must be positive, got {instruction.inst_data}"
                )
            # check if any loops have been entered or if the current loop was
            # already entered
            if len(loops) == 0 or loops[-1].idx_start != idx:
                loops.append(Loop(idx_start=idx, iterations_left=instruction.inst_data))
            idx += 1
        elif instruction.opcode == Opcode.END_LOOP:
            # grab the current loop
            if not loops:
                raise ValueError("END_LOOP encountered without a matching LOOP")
            loop = loops[-1]
            # decrease the iterations_left
            loop.iterations_left -= 1
            # break out if all iterations are complete
            if loop.iterations_left == 0:
                loops.pop()
                idx += 1
            else:
                idx = loop.idx_start
        # STOP opcode
        elif instruction.opcode == Opcode.STOP:
            break
        # BRANCH opcode
        elif instruction.opcode == Opcode.BRANCH:
            branch_target = instruction.inst_data
            # branch target might not be visited yet in this unrolled pass
            branch_index = _addresses.index(branch_target) if branch_target in _addresses else None
            break
        else:
            idx += 1

    return np.asarray(_duration), np.asarray(_flags), branch_index


@dataclass(frozen=True)
class InstructionSequence:
    instructions: List[Instruction]
    duration: npt.NDArray[np.int_] = field(init=False)
    flags: npt.NDArray[np.int_] = field(init=False)
    branch_index: Optional[int] = field(init=False)

    def __post_init__(self):
        duration, flags, branch_index = unroll_duration_flags(self.instructions)
        object.__setattr__(self, "duration", duration)
        object.__setattr__(self, "flags", flags)
        object.__setattr__(self, "branch_index", branch_index)
