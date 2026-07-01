"""Tests for the PulseBlaster device wrapper (spinapi calls are mocked)."""

from unittest.mock import patch

import pytest

from pulseblaster.device import PulseBlaster
from pulseblaster.read_code import code_to_instructions

# Valid against the default ESR-PRO 250 profile used by both the parser and program().
CODE = """
0xE00001, 100ns, CONTINUE
0xE00000, 200ns, BRANCH, 0
"""


@pytest.fixture
def pb():
    """A PulseBlaster instance with board selection/init mocked out."""
    with (
        patch("pulseblaster.device.pb_select_board", return_value=0),
        patch("pulseblaster.device.pb_init", return_value=0),
    ):
        yield PulseBlaster(0)


def test_program_succeeds_when_core_clock_returns_none(pb):
    """pb_core_clock wraps a void C call and returns None; program() must not raise.

    Regression test for the core-clock return handling: passing None into
    _check_return_code previously raised a spurious RuntimeError on every program().
    """
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
    """If a spinapi build returns a nonzero code, the guard still surfaces the failure."""
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
