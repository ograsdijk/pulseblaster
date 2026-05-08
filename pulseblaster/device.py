"""PulseBlaster device interface for programming and controlling the hardware."""

from collections.abc import Sequence

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
from .validation import ESR_PRO_250, BoardProfile, validate_sequence


class PulseBlaster:
    def __init__(
        self,
        board_number: int,
        profile: BoardProfile = ESR_PRO_250,
    ):
        self.board_number = board_number
        self.profile = profile

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
    def clock(self) -> float:
        """
        Clock speed [MHz]

        Returns:
            int: clock speed [MHz]
        """
        return self.profile.clock_mhz

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
        self.validate_program(sequence, profile=self.profile)

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

    @staticmethod
    def validate_program(
        sequence: Sequence[Instruction],
        profile: BoardProfile = ESR_PRO_250,
    ) -> None:
        """Validate instructions with the same profile used before programming."""
        validate_sequence(sequence, profile=profile)
