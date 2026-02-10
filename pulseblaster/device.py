"""
PulseBlaster device interface for programming and controlling the hardware.

This module provides a Python interface to the SpinCore PulseBlaster board
for programming instruction sequences and controlling execution.
"""

from typing import Sequence

from spinapi import (
    PULSE_PROGRAM,
    ns,
    pb_core_clock,
    pb_get_error,
    pb_get_firmware_id,
    pb_get_version,
    pb_init,
    pb_inst_pbonly,
    pb_read_status,
    pb_reset,
    pb_select_board,
    pb_start,
    pb_start_programming,
    pb_stop_programming,
)

from .data_structures import Instruction


class PulseBlaster:
    def __init__(
        self,
        board_number: int,
        clock: int = 250,
    ):
        self.board_number = board_number
        self._clock = clock

        self._check_return_code(
            pb_select_board(board_number),
            f"select PulseBlaster board {board_number}",
            error_cls=ConnectionError,
        )
        self._check_return_code(
            pb_init(),
            f"initialize PulseBlaster board {board_number}",
            error_cls=ConnectionError,
        )

    def _check_return_code(
        self,
        return_code: int,
        action: str,
        *,
        allow_non_negative: bool = False,
        error_cls: type[Exception] = RuntimeError,
    ) -> None:
        failed = return_code < 0 if allow_non_negative else return_code != 0
        if failed:
            raise error_cls(f"Failed to {action}: {pb_get_error()}")

    @staticmethod
    def _flags_to_int(flags: Sequence[int]) -> int:
        flags_int = 0
        for idx, flag in enumerate(flags):
            if flag:
                flags_int |= 1 << idx
        return flags_int

    @property
    def clock(self) -> int:
        """
        Clock speed [MHz]

        Returns:
            int: clock speed [MHz]
        """
        return self._clock

    @property
    def firmware_id(self) -> int:
        return pb_get_firmware_id()

    @property
    def version(self) -> str:
        return pb_get_version()

    @property
    def error(self) -> str:
        """
        Most recent error string

        Returns:
            str: error
        """
        return pb_get_error()

    @property
    def status(self) -> int:
        """
        Read status (4 bits)
        bit 0 - stopped
        bit 1 - reset
        bit 2 - running
        bit 3 - waiting

        Returns:
            int: status bit
        """
        return pb_read_status()

    def program(self, sequence: Sequence[Instruction]) -> None:
        """
        Program the PulseBlaster with a sequence of instructions.

        Args:
            sequence (Sequence[Instruction]): sequence of instructions to program
        """
        self._check_return_code(pb_reset(), "reset PulseBlaster board")
        self._check_return_code(pb_core_clock(self.clock), f"set core clock to {self.clock} MHz")
        self._check_return_code(pb_start_programming(PULSE_PROGRAM), "start pulse programming")

        try:
            for idx, seq in enumerate(sequence):
                flags_int = self._flags_to_int(seq.flags)
                self._check_return_code(
                    pb_inst_pbonly(flags_int, seq.opcode, seq.inst_data, seq.duration * ns),
                    f"write instruction {idx}",
                    allow_non_negative=True,
                )
        finally:
            self._check_return_code(pb_stop_programming(), "stop pulse programming")

    def start(self) -> None:
        """Start the PulseBlaster program execution."""
        self._check_return_code(pb_start(), "start PulseBlaster execution")

    def reset(self) -> None:
        """Reset the PulseBlaster board."""
        self._check_return_code(pb_reset(), "reset PulseBlaster board")
