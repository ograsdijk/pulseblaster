"""Tests for the PulseBlaster device wrapper (spinapi calls are mocked)."""

from unittest.mock import call, patch

import pytest

from pulseblaster import ESR_PRO_250, PulseBlaster, PulseBlasterStatus
from pulseblaster.read_code import code_to_instructions

CODE = """
0xE00001, 100ns, CONTINUE
0xE00000, 200ns, BRANCH, 0
"""


@pytest.fixture
def pb():
    with (
        patch("pulseblaster.device.pb_select_board", return_value=0),
        patch("pulseblaster.device.pb_init", return_value=0),
    ):
        yield PulseBlaster(0, profile="ESR_PRO_250")


def test_named_profile_is_resolved(pb):
    assert pb.profile is ESR_PRO_250
    assert pb.clock == 250.0


def test_unknown_profile_fails_before_hardware_access():
    with pytest.raises(ValueError, match="Unknown board profile"):
        PulseBlaster(0, profile="does-not-exist")


def test_program_succeeds_when_core_clock_returns_none(pb):
    sequence = code_to_instructions(CODE).instructions
    with (
        patch("pulseblaster.device.pb_reset", return_value=0),
        patch("pulseblaster.device.pb_core_clock", return_value=None) as core_clock,
        patch("pulseblaster.device.pb_start_programming", return_value=0),
        patch("pulseblaster.device.pb_inst_pbonly", return_value=0),
        patch("pulseblaster.device.pb_stop_programming", return_value=0),
    ):
        pb.program(sequence)

    core_clock.assert_called_once_with(pb.clock)


def test_program_checks_core_clock_when_code_returned(pb):
    sequence = code_to_instructions(CODE).instructions
    with (
        patch("pulseblaster.device.pb_reset", return_value=0),
        patch("pulseblaster.device.pb_core_clock", return_value=-1),
        patch("pulseblaster.device.pb_start_programming", return_value=0),
        patch("pulseblaster.device.pb_inst_pbonly", return_value=0),
        patch("pulseblaster.device.pb_stop_programming", return_value=0),
        patch("pulseblaster.device.pb_get_error", return_value="bad clock"),
        pytest.raises(RuntimeError, match="set core clock"),
    ):
        pb.program(sequence)


def test_status_is_structured(pb):
    with patch("pulseblaster.device.pb_read_status", return_value=0b1100):
        status = pb.status

    assert status == PulseBlasterStatus(raw=0b1100, state="waiting")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (0b1000, "waiting"),
        (0b0100, "running"),
        (0b0001, "stopped"),
        (0b0000, "reset"),
        (0b0010, "idle"),
        (0b1100, "waiting"),
        (0b0101, "running"),
        (0b1_0000, "unknown"),
        (-1, "unknown"),
    ],
)
def test_status_state_precedence(raw, expected):
    assert PulseBlasterStatus.from_raw(raw).state == expected


def test_status_read_error_is_reported(pb):
    with (
        patch("pulseblaster.device.pb_read_status", return_value=-1),
        patch("pulseblaster.device.pb_get_error", return_value="status failed"),
        pytest.raises(RuntimeError, match="status failed"),
    ):
        _ = pb.status


def test_start_stop_reset_close_delegate_to_spinapi(pb):
    with (
        patch("pulseblaster.device.pb_start", return_value=0) as start,
        patch("pulseblaster.device.pb_stop", return_value=0) as stop,
        patch("pulseblaster.device.pb_reset", return_value=0) as reset,
        patch("pulseblaster.device.pb_close", return_value=0) as close,
    ):
        pb.start()
        pb.stop()
        pb.reset()
        pb.close()

    start.assert_called_once_with()
    stop.assert_called_once_with()
    reset.assert_called_once_with()
    close.assert_called_once_with()


def test_stop_and_close_accept_void_spinapi_wrappers(pb):
    with (
        patch("pulseblaster.device.pb_stop", return_value=None),
        patch("pulseblaster.device.pb_close", return_value=None),
    ):
        pb.stop()
        pb.close()


def test_each_operation_reselects_its_own_board():
    with (
        patch("pulseblaster.device.pb_select_board", return_value=0) as select_board,
        patch("pulseblaster.device.pb_init", return_value=0),
        patch("pulseblaster.device.pb_start", return_value=0),
    ):
        board0 = PulseBlaster(0)
        PulseBlaster(1)
        board0.start()

    assert select_board.call_args_list == [call(0), call(1), call(0)]
