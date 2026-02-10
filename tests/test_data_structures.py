"""Tests for data_structures module."""

import numpy as np
import pytest
from spinapi import BRANCH, CONTINUE, END_LOOP, JSR, LONG_DELAY, LOOP, RTS, STOP, WAIT

from pulseblaster.data_structures import (
    Instruction,
    InstructionSequence,
    Loop,
    Opcode,
    Pulse,
    Signal,
    unroll_duration_flags,
)


class TestPulse:
    """Tests for Pulse dataclass."""

    def test_pulse_creation(self):
        """Test creating a valid Pulse."""
        pulse = Pulse(
            period=1000,
            channels=[0, 1],
            offset=100,
            high=500,
            active_high=True,
        )
        assert pulse.period == 1000
        assert pulse.channels == [0, 1]
        assert pulse.offset == 100
        assert pulse.high == 500
        assert pulse.active_high is True

    def test_pulse_with_active_low(self):
        """Test creating a Pulse with active_high=False."""
        pulse = Pulse(
            period=2000,
            channels=[5],
            offset=0,
            high=1000,
            active_high=False,
        )
        assert pulse.active_high is False


class TestSignal:
    """Tests for Signal dataclass."""

    def test_signal_with_duty_cycle(self):
        """Test Signal creation with duty cycle."""
        signal = Signal(frequency=10, channels=[0], duty_cycle=0.3)
        assert signal.frequency == 10
        assert signal.duty_cycle == 0.3
        assert signal.high > 0
        assert signal._duty_cycle_set is True

    def test_signal_with_high_time(self):
        """Test Signal creation with explicit high time."""
        signal = Signal(frequency=100, channels=[1], high=1000000)
        assert signal.high == 1000000
        assert signal._duty_cycle_set is False
        assert 0 < signal.duty_cycle < 1

    def test_signal_default_duty_cycle(self):
        """Test Signal with default 50% duty cycle."""
        signal = Signal(frequency=50, channels=[2])
        assert signal.duty_cycle == 0.5
        expected_high = int((1 / 50 * 1e9) * 0.5)
        assert signal.high == expected_high

    def test_signal_validation_negative_frequency(self):
        """Test that negative frequency raises ValueError."""
        with pytest.raises(ValueError, match="Frequency must be positive"):
            Signal(frequency=-10, channels=[0])

    def test_signal_validation_zero_frequency(self):
        """Test that zero frequency raises ValueError."""
        with pytest.raises(ValueError, match="Frequency must be positive"):
            Signal(frequency=0, channels=[0])

    def test_signal_validation_negative_offset(self):
        """Test that negative offset raises ValueError."""
        with pytest.raises(ValueError, match="Offset must be non-negative"):
            Signal(frequency=10, channels=[0], offset=-100)

    def test_signal_validation_negative_high(self):
        """Test that negative high time raises ValueError."""
        with pytest.raises(ValueError, match="High time must be non-negative"):
            Signal(frequency=10, channels=[0], high=-500)

    def test_signal_validation_invalid_duty_cycle_too_high(self):
        """Test that duty cycle > 1 raises ValueError."""
        with pytest.raises(ValueError, match="Duty cycle must be between 0 and 1"):
            Signal(frequency=10, channels=[0], duty_cycle=1.5)

    def test_signal_validation_invalid_duty_cycle_negative(self):
        """Test that negative duty cycle raises ValueError."""
        with pytest.raises(ValueError, match="Duty cycle must be between 0 and 1"):
            Signal(frequency=10, channels=[0], duty_cycle=-0.1)

    def test_signal_validation_empty_channels(self):
        """Test that empty channels list raises ValueError."""
        with pytest.raises(ValueError, match="At least one channel must be specified"):
            Signal(frequency=10, channels=[])

    def test_signal_validation_invalid_channel_negative(self):
        """Test that negative channel number raises ValueError."""
        with pytest.raises(ValueError, match="Channels must be non-negative"):
            Signal(frequency=10, channels=[-1])

    def test_signal_allows_high_channel_numbers(self):
        """Signal channel validation is hardware-agnostic and allows high indices."""
        signal = Signal(frequency=10, channels=[24])
        assert signal.channels == [24]

    def test_signal_validation_high_exceeds_period(self):
        """Test that high time exceeding period raises ValueError."""
        with pytest.raises(ValueError, match="Pulse high.*>= period"):
            Signal(frequency=10, channels=[0], high=int(2e9))

    def test_signal_with_offset(self):
        """Test Signal with offset."""
        signal = Signal(frequency=20, channels=[3], offset=500000)
        assert signal.offset == 500000

    def test_signal_multiple_channels(self):
        """Test Signal with multiple channels."""
        signal = Signal(frequency=15, channels=[1, 2, 3, 5])
        assert signal.channels == [1, 2, 3, 5]

    def test_signal_active_low(self):
        """Test Signal with active_high=False."""
        signal = Signal(frequency=25, channels=[4], active_high=False)
        assert signal.active_high is False


class TestOpcode:
    """Tests for Opcode enum."""

    def test_opcode_values(self):
        """Test that Opcode enum has expected values."""
        assert Opcode.CONTINUE == CONTINUE
        assert Opcode.STOP == STOP
        assert Opcode.LOOP == LOOP
        assert Opcode.END_LOOP == END_LOOP
        assert Opcode.JSR == JSR
        assert Opcode.RTS == RTS
        assert Opcode.BRANCH == BRANCH
        assert Opcode.LONG_DELAY == LONG_DELAY
        assert Opcode.WAIT == WAIT


class TestInstruction:
    """Tests for Instruction dataclass."""

    def test_instruction_creation(self):
        """Test creating a valid Instruction."""
        instruction = Instruction(
            label="start",
            flags=[1, 0, 1, 0] + [0] * 20,
            duration=1000,
            opcode=Opcode.CONTINUE,
            inst_data=0,
        )
        assert instruction.label == "start"
        assert len(instruction.flags) == 24
        assert instruction.duration == 1000
        assert instruction.opcode == Opcode.CONTINUE
        assert instruction.inst_data == 0

    def test_instruction_with_loop(self):
        """Test Instruction with LOOP opcode."""
        instruction = Instruction(
            label="",
            flags=[0] * 24,
            duration=500,
            opcode=Opcode.LOOP,
            inst_data=10,
        )
        assert instruction.opcode == Opcode.LOOP
        assert instruction.inst_data == 10

    def test_instruction_default_inst_data(self):
        """Test Instruction with default inst_data."""
        instruction = Instruction(
            label="",
            flags=[0] * 24,
            duration=100,
            opcode=Opcode.CONTINUE,
        )
        assert instruction.inst_data == 0


class TestLoop:
    """Tests for Loop dataclass."""

    def test_loop_creation(self):
        """Test creating a Loop."""
        loop = Loop(idx_start=5, iterations_left=10)
        assert loop.idx_start == 5
        assert loop.iterations_left == 10


class TestUnrollDurationFlags:
    """Tests for unroll_duration_flags function."""

    def test_simple_sequence(self):
        """Test unrolling a simple sequence without loops or branches."""
        instructions = [
            Instruction("", [1] * 24, 100, Opcode.CONTINUE, 0),
            Instruction("", [0] * 24, 200, Opcode.CONTINUE, 0),
            Instruction("", [1] * 24, 150, Opcode.STOP, 0),
        ]
        duration, flags, branch_idx = unroll_duration_flags(instructions)
        assert len(duration) == 3
        assert len(flags) == 3
        assert branch_idx is None
        assert np.array_equal(duration, [100, 200, 150])

    def test_loop_sequence(self):
        """Test unrolling a sequence with a loop."""
        instructions = [
            Instruction("", [1] * 24, 100, Opcode.LOOP, 3),
            Instruction("", [0] * 24, 200, Opcode.END_LOOP, 0),
            Instruction("", [1] * 24, 300, Opcode.STOP, 0),
        ]
        duration, flags, branch_idx = unroll_duration_flags(instructions)
        # Loop runs 3 times: [100, 200] * 3 + [300]
        assert len(duration) == 7  # 3*2 + 1
        assert branch_idx is None

    def test_branch_sequence(self):
        """Test unrolling a sequence with a branch."""
        instructions = [
            Instruction("start", [1] * 24, 100, Opcode.CONTINUE, 0),
            Instruction("", [0] * 24, 200, Opcode.BRANCH, 0),
        ]
        duration, flags, branch_idx = unroll_duration_flags(instructions)
        assert branch_idx == 0  # branches back to index 0
        assert len(duration) == 2

    def test_jsr_rts_sequence(self):
        """Test unrolling a sequence with JSR and RTS."""
        instructions = [
            Instruction("", [1] * 24, 100, Opcode.CONTINUE, 0),
            Instruction("", [0] * 24, 200, Opcode.JSR, 3),  # Jump to index 3
            Instruction("", [1] * 24, 300, Opcode.STOP, 0),
            Instruction("sub", [1] * 24, 400, Opcode.CONTINUE, 0),
            Instruction("", [0] * 24, 500, Opcode.RTS, 0),
        ]
        duration, flags, branch_idx = unroll_duration_flags(instructions)
        # Should execute: 0, 1 (JSR to 3), 3, 4 (RTS to 2), 2 (STOP)
        assert len(duration) == 5
        assert branch_idx is None


class TestInstructionSequence:
    """Tests for InstructionSequence dataclass."""

    def test_instruction_sequence_creation(self):
        """Test creating an InstructionSequence."""
        instructions = [
            Instruction("", [1] * 24, 100, Opcode.CONTINUE, 0),
            Instruction("", [0] * 24, 200, Opcode.STOP, 0),
        ]
        seq = InstructionSequence(instructions)
        assert len(seq.instructions) == 2
        assert len(seq.duration) == 2
        assert len(seq.flags) == 2
        assert seq.branch_index is None

    def test_instruction_sequence_with_branch(self):
        """Test InstructionSequence with branch."""
        instructions = [
            Instruction("start", [1] * 24, 100, Opcode.CONTINUE, 0),
            Instruction("", [0] * 24, 200, Opcode.BRANCH, 0),
        ]
        seq = InstructionSequence(instructions)
        assert seq.branch_index == 0

    def test_instruction_sequence_frozen(self):
        """Test that InstructionSequence is frozen."""
        instructions = [
            Instruction("", [1] * 24, 100, Opcode.STOP, 0),
        ]
        seq = InstructionSequence(instructions)
        # Should not be able to modify duration directly
        with pytest.raises(AttributeError):
            seq.duration = np.array([500])
