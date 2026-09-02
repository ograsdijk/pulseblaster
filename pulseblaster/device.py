"""PulseBlaster device interface for programming and controlling the hardware."""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from threading import RLock
from typing import Literal

from spinapi import (
    PULSE_PROGRAM,
    ns,
    pb_close,
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
    pb_stop,
    pb_stop_programming,
)

from .data_structures import Instruction
from .validation import ESR_PRO_250, BoardProfile, get_board_profile, validate_sequence

PulseBlasterState = Literal["idle", "stopped", "reset", "running", "waiting", "unknown"]

# SpinAPI keeps the selected board in process-global state. Selection and the
# operation that follows it therefore have to be atomic across PulseBlaster objects.
_SPINAPI_LOCK = RLock()


@dataclass(frozen=True)
class PulseBlasterStatus:
    """PulseBlaster execution status with the raw SpinAPI bit mask."""

    raw: int
    state: PulseBlasterState

    @classmethod
    def from_raw(cls, raw: int) -> "PulseBlasterStatus":
        """Convert SpinAPI's independent status bits into one canonical state."""
        # PulseBlaster status uses the low four bits. Treat negative values or
        # unexpected higher bits conservatively rather than folding a future or
        # incompatible status encoding into one of the known states.
        if raw < 0 or raw & ~0b1111:
            state: PulseBlasterState = "unknown"
        # WAITING is also RUNNING on PulseBlaster hardware, so use the more
        # specific state first. The Reset bit has unusual semantics: SpinAPI
        # reports it high after initialization and low after reset until the
        # board is triggered again. Thus bit 1 high with no active execution or
        # stopped state is a normal idle condition, not an error.
        elif raw & 0b1000:
            state = "waiting"
        elif raw & 0b0100:
            state = "running"
        elif raw & 0b0001:
            state = "stopped"
        elif not raw & 0b0010:
            state = "reset"
        else:
            state = "idle"
        return cls(raw=raw, state=state)


class PulseBlaster:
    def __init__(
        self,
        board_number: int,
        profile: BoardProfile | str = ESR_PRO_250,
    ) -> None:
        self.board_number = int(board_number)
        self.profile = get_board_profile(profile)

        with _SPINAPI_LOCK:
            self._select_board(error_cls=ConnectionError)
            self._check_return_code(
                pb_init(),
                f"initialize PulseBlaster board {self.board_number}",
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

    def _check_optional_return_code(
        self,
        return_code: int | None,
        action: str,
        *,
        error_cls: type[Exception] = RuntimeError,
    ) -> None:
        if return_code is not None:
            self._check_return_code(return_code, action, error_cls=error_cls)

    def _select_board(self, *, error_cls: type[Exception] = RuntimeError) -> None:
        self._check_return_code(
            pb_select_board(self.board_number),
            f"select PulseBlaster board {self.board_number}",
            error_cls=error_cls,
        )

    @contextmanager
    def _selected_board(self) -> Iterator[None]:
        """Select this object's board and hold SpinAPI's global-state lock."""
        with _SPINAPI_LOCK:
            self._select_board()
            yield

    @staticmethod
    def _flags_to_int(flags: Sequence[int]) -> int:
        flags_int = 0
        for idx, flag in enumerate(flags):
            if flag:
                flags_int |= 1 << idx
        return flags_int

    @property
    def clock(self) -> float:
        """Clock speed in MHz from the selected board profile."""
        return self.profile.clock_mhz

    @property
    def firmware_id(self) -> int:
        with self._selected_board():
            return pb_get_firmware_id()

    @property
    def version(self) -> str:
        return pb_get_version()

    @property
    def error(self) -> str:
        return pb_get_error()

    @property
    def raw_status(self) -> int:
        """Raw SpinAPI execution-status bit mask."""
        with self._selected_board():
            raw = pb_read_status()
            if raw < 0:
                raise RuntimeError(f"Failed to read PulseBlaster status: {pb_get_error()}")
            return raw

    @property
    def status(self) -> PulseBlasterStatus:
        """Current execution status as one canonical state plus the raw bit mask."""
        return PulseBlasterStatus.from_raw(self.raw_status)

    def program(self, sequence: Sequence[Instruction]) -> None:
        """Program the PulseBlaster with a sequence of instructions."""
        self.validate_program(sequence, profile=self.profile)

        with self._selected_board():
            self._check_return_code(pb_reset(), "reset PulseBlaster board")
            self._check_optional_return_code(
                pb_core_clock(self.clock),
                f"set core clock to {self.clock} MHz",
            )
            self._check_return_code(
                pb_start_programming(PULSE_PROGRAM),
                "start pulse programming",
            )

            try:
                for idx, instruction in enumerate(sequence):
                    self._check_return_code(
                        pb_inst_pbonly(
                            self._flags_to_int(instruction.flags),
                            instruction.opcode,
                            instruction.inst_data,
                            instruction.duration * ns,
                        ),
                        f"write instruction {idx}",
                        allow_non_negative=True,
                    )
            finally:
                self._check_return_code(pb_stop_programming(), "stop pulse programming")

    def start(self) -> None:
        with self._selected_board():
            self._check_return_code(pb_start(), "start PulseBlaster execution")

    def stop(self) -> None:
        """Stop execution without changing the current TTL output levels."""
        with self._selected_board():
            self._check_optional_return_code(pb_stop(), "stop PulseBlaster execution")

    def reset(self) -> None:
        with self._selected_board():
            self._check_return_code(pb_reset(), "reset PulseBlaster board")

    def close(self) -> None:
        """Release the SpinAPI connection without implicitly stopping execution."""
        with self._selected_board():
            self._check_optional_return_code(pb_close(), "close PulseBlaster board")

    @staticmethod
    def validate_program(
        sequence: Sequence[Instruction],
        profile: BoardProfile = ESR_PRO_250,
    ) -> None:
        validate_sequence(sequence, profile=profile)
