"""Tests for ESR-PRO board profiles and instruction-sequence validation."""

import numpy as np
import pytest

from pulseblaster.data_structures import Instruction, InstructionSequence, Opcode
from pulseblaster.validation import ESR_PRO_250, BoardProfile, validate_sequence


def _flags(
    output_channels: list[int] | None = None,
    *,
    control_mode: int = 7,
    profile: BoardProfile = ESR_PRO_250,
) -> list[int]:
    flags = [0] * profile.flag_bits
    if output_channels:
        for channel in output_channels:
            flags[channel] = 1
    b0, b1, b2 = profile.control_bits
    flags[b0] = control_mode & 1
    flags[b1] = (control_mode >> 1) & 1
    flags[b2] = (control_mode >> 2) & 1
    return flags


class TestBoardProfile:
    def test_esr_pro_250_defaults(self):
        assert ESR_PRO_250.clock_mhz == 250.0
        assert ESR_PRO_250.flag_bits == 24
        assert ESR_PRO_250.output_bits == 21
        assert ESR_PRO_250.control_bits == (21, 22, 23)
        assert ESR_PRO_250.min_instruction_cycles == 6
        assert ESR_PRO_250.min_instruction_len_ns == 24
        assert ESR_PRO_250.short_pulse_disable_modes == frozenset({0, 7})

    def test_control_bits_must_be_valid(self):
        with pytest.raises(ValueError, match="control_bits out of range"):
            BoardProfile(flag_bits=8, output_bits=5, control_bits=(5, 6, 8))


class TestValidateSequence:
    def test_active_output_with_on_disable_mode_is_valid(self):
        instructions = [
            Instruction("", _flags([0], control_mode=7), 24, Opcode.CONTINUE, 0),
            Instruction("", _flags(control_mode=7), 24, Opcode.BRANCH, 0),
        ]
        validate_sequence(instructions)

    def test_active_output_with_zero_disable_mode_is_valid(self):
        instructions = [Instruction("", _flags([0], control_mode=0), 24, Opcode.STOP, 0)]
        validate_sequence(instructions)

    def test_invalid_control_mode_rejected(self):
        instructions = [Instruction("", _flags([0], control_mode=6), 24, Opcode.STOP, 0)]
        with pytest.raises(ValueError, match="invalid control mode"):
            validate_sequence(instructions)

    def test_short_pulse_mode_requires_matching_duration(self):
        instructions = [Instruction("", _flags([0], control_mode=5), 24, Opcode.STOP, 0)]
        with pytest.raises(ValueError, match="expected mode 6"):
            validate_sequence(instructions)

    def test_short_pulse_mode_with_matching_duration_is_valid(self):
        profile = BoardProfile(min_instruction_cycles=1)
        instructions = [
            Instruction("", _flags([0], control_mode=5, profile=profile), 20, Opcode.STOP, 0)
        ]
        validate_sequence(instructions, profile=profile)

    def test_duration_must_align_to_clock(self):
        instructions = [Instruction("", _flags([0], control_mode=7), 25, Opcode.STOP, 0)]
        with pytest.raises(ValueError, match="does not align to clock"):
            validate_sequence(instructions)

    def test_duration_must_meet_minimum_cycles(self):
        instructions = [Instruction("", _flags([0], control_mode=7), 20, Opcode.STOP, 0)]
        with pytest.raises(ValueError, match="below minimum"):
            validate_sequence(instructions)

    def test_non_output_non_control_flag_is_rejected(self):
        profile = BoardProfile(output_bits=20)
        flags = _flags(control_mode=7, profile=profile)
        flags[20] = 1
        instructions = [Instruction("", flags, 24, Opcode.STOP, 0)]
        with pytest.raises(ValueError, match="non-output flag bits"):
            validate_sequence(instructions, profile=profile)

    def test_long_delay_multiplier_must_be_at_least_two(self):
        instructions = [Instruction("", _flags(control_mode=7), 24, Opcode.LONG_DELAY, 1)]
        with pytest.raises(ValueError, match="LONG_DELAY multiplier"):
            validate_sequence(instructions)

    def test_long_delay_unrolls_effective_duration(self):
        sequence = InstructionSequence(
            [
                Instruction("", _flags(control_mode=7), 24, Opcode.LONG_DELAY, 2),
                Instruction("", _flags(control_mode=7), 24, Opcode.STOP, 0),
            ]
        )
        assert np.array_equal(sequence.duration, [48, 24])

    def test_end_loop_must_match_latest_loop(self):
        instructions = [
            Instruction("", _flags([0]), 24, Opcode.LOOP, 2),
            Instruction("", _flags([0]), 24, Opcode.CONTINUE, 0),
            Instruction("", _flags([0]), 24, Opcode.END_LOOP, 1),
        ]
        with pytest.raises(ValueError, match="expected 0"):
            validate_sequence(instructions)

    def test_missing_end_loop_raises(self):
        instructions = [
            Instruction("", _flags([0]), 24, Opcode.LOOP, 2),
            Instruction("", _flags([0]), 24, Opcode.STOP, 0),
        ]
        with pytest.raises(ValueError, match="Missing END_LOOP"):
            validate_sequence(instructions)

    def test_wait_not_first(self):
        instructions = [Instruction("", _flags(control_mode=7), 24, Opcode.WAIT, 0)]
        with pytest.raises(ValueError, match="WAIT is not allowed as the first instruction"):
            validate_sequence(instructions)
