from typing import Sequence

import numpy as np
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

        pb_select_board(board_number)
        if pb_init() != 0:
            raise ConnectionError(
                f"Could not initialize PulseBlaster board {board_number}"
            )

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
        return pb_read_status

    def program(self, sequence: Sequence[Instruction]) -> None:
        pb_reset()
        pb_core_clock(self.clock)
        pb_start_programming(PULSE_PROGRAM)

        for seq in sequence:
            flags_int = np.sum([1 << i if v else 0 for i,v in enumerate(seq.flags)])
            pb_inst_pbonly(flags_int, seq.opcode, seq.inst_data, seq.duration * ns)
        pb_stop_programming()

    def start(self) -> None:
        pb_start()
