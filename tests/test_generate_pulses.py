"""Tests for generate_pulses module."""

import pytest

from pulseblaster.data_structures import InstructionSequence, Opcode, Pulse, Signal
from pulseblaster.generate_pulses import (
    calculate_resets,
    calculate_resets_masking,
    generate_repeating_pulses,
    minimum_duration_and_num_cycles,
    pulses_convert_to_instruction_length,
    rescale_pulses,
)


class TestMinimumDurationAndNumCycles:
    """Tests for minimum_duration_and_num_cycles function."""

    def test_simple_frequencies(self):
        """Test with simple integer multiple frequencies."""
        signals = [
            Signal(frequency=10, channels=[0]),
            Signal(frequency=20, channels=[1]),
        ]
        duration, nr_cycles, frequencies = minimum_duration_and_num_cycles(
            signals, [], None, 20
        )
        assert duration > 0
        assert nr_cycles > 0
        assert len(frequencies) == 2

    def test_with_masking_signals(self):
        """Test with masking signals."""
        signals = [Signal(frequency=10, channels=[0])]
        masking_signals = [Signal(frequency=5, channels=[1])]
        duration, nr_cycles, frequencies = minimum_duration_and_num_cycles(
            signals, masking_signals, None, 20
        )
        assert duration > 0
        assert nr_cycles > 0
        assert len(frequencies) == 2

    def test_with_specified_duration(self):
        """Test with pre-specified duration."""
        signals = [Signal(frequency=10, channels=[0])]
        duration, nr_cycles, frequencies = minimum_duration_and_num_cycles(
            signals, [], 1000000, 20
        )
        assert nr_cycles == 1000000 // 20

    def test_frequency_rescaling(self):
        """Test that frequencies are properly rescaled."""
        signals = [
            Signal(frequency=100, channels=[0]),
            Signal(frequency=200, channels=[1]),
        ]
        duration, nr_cycles, frequencies = minimum_duration_and_num_cycles(
            signals, [], None, 20
        )
        # Both frequencies should be rescaled but maintain their ratio
        assert len(frequencies) == 2
        assert frequencies[0] > 0
        assert frequencies[1] > 0


class TestPulsesConvertToInstructionLength:
    """Tests for pulses_convert_to_instruction_length function."""

    def test_simple_conversion(self):
        """Test simple conversion to instruction lengths."""
        signals = [Signal(frequency=10, channels=[0], duty_cycle=0.5)]
        pulses, cycles = pulses_convert_to_instruction_length(signals, 20)
        assert len(pulses) == 1
        assert isinstance(pulses[0], Pulse)
        assert len(cycles) > 0

    def test_with_offset(self):
        """Test conversion with offset."""
        signals = [Signal(frequency=10, channels=[0], offset=1000)]
        pulses, cycles = pulses_convert_to_instruction_length(signals, 20)
        assert pulses[0].offset > 0
        assert pulses[0].offset == 1000 // 20

    def test_multiple_signals(self):
        """Test conversion with multiple signals."""
        signals = [
            Signal(frequency=10, channels=[0]),
            Signal(frequency=20, channels=[1]),
        ]
        pulses, cycles = pulses_convert_to_instruction_length(signals, 20)
        assert len(pulses) == 2
        assert pulses[0].period != pulses[1].period


class TestRescalePulses:
    """Tests for rescale_pulses function."""

    def test_rescale_by_gcd(self):
        """Test rescaling pulses by GCD."""
        pulses = [
            Pulse(period=1000, channels=[0], offset=200, high=500, active_high=True),
        ]
        gcd_cycles = 10
        rescaled, _ = rescale_pulses(gcd_cycles, pulses, None)
        assert rescaled[0].period == 100
        assert rescaled[0].offset == 20
        assert rescaled[0].high == 50

    def test_rescale_with_masking(self):
        """Test rescaling with masking pulses."""
        pulses = [
            Pulse(period=1000, channels=[0], offset=0, high=500, active_high=True),
        ]
        masking_pulses = [
            Pulse(period=2000, channels=[1], offset=0, high=1000, active_high=True),
        ]
        gcd_cycles = 10
        rescaled_pulses, rescaled_masking = rescale_pulses(
            gcd_cycles, pulses, masking_pulses
        )
        assert rescaled_pulses[0].period == 100
        assert rescaled_masking[0].period == 200

    def test_no_rescale_when_gcd_is_one(self):
        """Test that no rescaling happens when GCD is 1."""
        pulses = [
            Pulse(period=1000, channels=[0], offset=0, high=500, active_high=True),
        ]
        gcd_cycles = 1
        rescaled, _ = rescale_pulses(gcd_cycles, pulses, None)
        assert rescaled[0].period == 1000
        assert rescaled[0].high == 500


class TestCalculateResets:
    """Tests for calculate_resets function."""

    def test_simple_reset(self):
        """Test simple reset calculation."""
        pulses = [
            Pulse(period=10, channels=[0], offset=0, high=5, active_high=True),
        ]
        elapsed = [0]
        channels, new_elapsed = calculate_resets(elapsed, pulses, 0)
        assert 0 in channels
        assert new_elapsed[0] == 1

    def test_with_offset(self):
        """Test reset with offset."""
        pulses = [
            Pulse(period=10, channels=[0], offset=5, high=3, active_high=True),
        ]
        elapsed = [0]
        # Before offset
        channels, new_elapsed = calculate_resets(elapsed, pulses, 3)
        assert 0 not in channels
        # After offset
        channels, new_elapsed = calculate_resets(elapsed, pulses, 5)
        assert 0 in channels

    def test_period_wraparound(self):
        """Test that elapsed time wraps around at period."""
        pulses = [
            Pulse(period=5, channels=[0], offset=0, high=2, active_high=True),
        ]
        elapsed = [4]
        channels, new_elapsed = calculate_resets(elapsed, pulses, 10)
        assert new_elapsed[0] == 0  # Should wrap around

    def test_multiple_pulses(self):
        """Test with multiple pulses."""
        pulses = [
            Pulse(period=10, channels=[0], offset=0, high=5, active_high=True),
            Pulse(period=10, channels=[1], offset=0, high=3, active_high=True),
        ]
        elapsed = [0, 0]
        channels, new_elapsed = calculate_resets(elapsed, pulses, 0)
        assert 0 in channels
        assert 1 in channels


class TestCalculateResetsMasking:
    """Tests for calculate_resets_masking function."""

    def test_masking_channels(self):
        """Test masking channel calculation."""
        pulses = [
            Pulse(period=10, channels=[0, 1], offset=0, high=5, active_high=True),
        ]
        masking_pulses = [
            Pulse(period=10, channels=[1], offset=0, high=3, active_high=True),
        ]
        elapsed = [0]
        channels, new_elapsed = calculate_resets_masking(
            elapsed, pulses, masking_pulses, 0
        )
        # Channel 0 should be in the list, channel 1 should be removed during high
        assert len(channels) >= 1

    def test_no_masking(self):
        """Test when no masking occurs."""
        pulses = [
            Pulse(period=10, channels=[0], offset=0, high=5, active_high=True),
        ]
        masking_pulses = [
            Pulse(period=10, channels=[1], offset=0, high=8, active_high=True),
        ]
        elapsed = [0]
        channels, new_elapsed = calculate_resets_masking(
            elapsed, pulses, masking_pulses, 0
        )
        # Channel 0 should still be in the list
        assert 0 in channels


class TestGenerateRepeatingPulses:
    """Tests for generate_repeating_pulses function."""

    def test_simple_pulse_generation(self):
        """Test generating a simple repeating pulse."""
        signals = [Signal(frequency=10, channels=[0], duty_cycle=0.5)]
        sequence = generate_repeating_pulses(signals, progress=False)
        assert isinstance(sequence, InstructionSequence)
        assert len(sequence.instructions) > 0
        # Last instruction should be BRANCH
        assert sequence.instructions[-1].opcode == Opcode.BRANCH

    def test_multiple_signals(self):
        """Test generating multiple signals."""
        signals = [
            Signal(frequency=10, channels=[0]),
            Signal(frequency=20, channels=[1]),
        ]
        sequence = generate_repeating_pulses(signals, progress=False)
        assert isinstance(sequence, InstructionSequence)
        assert len(sequence.instructions) > 0

    def test_with_masking_signals(self):
        """Test generating pulses with masking signals."""
        signals = [Signal(frequency=10, channels=[0])]
        masking_signals = [Signal(frequency=5, channels=[0])]
        sequence = generate_repeating_pulses(
            signals, masking_signals=masking_signals, progress=False
        )
        assert isinstance(sequence, InstructionSequence)
        assert len(sequence.instructions) > 0

    def test_with_offset(self):
        """Test generating pulses with offset."""
        signals = [
            Signal(frequency=10, channels=[0], offset=0),
            Signal(frequency=10, channels=[1], offset=50000000),
        ]
        sequence = generate_repeating_pulses(signals, progress=False)
        assert isinstance(sequence, InstructionSequence)
        assert len(sequence.instructions) > 0

    def test_active_low_signal(self):
        """Test generating pulses with active_low signals."""
        signals = [Signal(frequency=10, channels=[0], active_high=False)]
        sequence = generate_repeating_pulses(signals, progress=False)
        assert isinstance(sequence, InstructionSequence)
        assert len(sequence.instructions) > 0

    def test_validation_empty_signals(self):
        """Test that empty signals list raises ValueError."""
        with pytest.raises(ValueError, match="At least one signal must be provided"):
            generate_repeating_pulses([], progress=False)

    def test_validation_negative_min_instruction_len(self):
        """Test that negative min_instruction_len raises ValueError."""
        signals = [Signal(frequency=10, channels=[0])]
        with pytest.raises(ValueError, match="min_instruction_len must be positive"):
            generate_repeating_pulses(signals, min_instruction_len=-10, progress=False)

    def test_validation_zero_min_instruction_len(self):
        """Test that zero min_instruction_len raises ValueError."""
        signals = [Signal(frequency=10, channels=[0])]
        with pytest.raises(ValueError, match="min_instruction_len must be positive"):
            generate_repeating_pulses(signals, min_instruction_len=0, progress=False)

    def test_validation_negative_nr_channels(self):
        """Test that negative nr_channels raises ValueError."""
        signals = [Signal(frequency=10, channels=[0])]
        with pytest.raises(ValueError, match="nr_channels must be positive"):
            generate_repeating_pulses(signals, nr_channels=-1, progress=False)

    def test_validation_reserved_channels_too_large(self):
        """Test that reserved channels must be smaller than total channel count."""
        signals = [Signal(frequency=10, channels=[0])]
        with pytest.raises(ValueError, match="reserved_channels"):
            generate_repeating_pulses(
                signals, nr_channels=4, reserved_channels=4, progress=False
            )

    def test_validation_signal_channel_exceeds_controllable_range(self):
        """Test that requested channels fit in configured controllable channel range."""
        signals = [Signal(frequency=10, channels=[21])]
        with pytest.raises(ValueError, match="controllable range"):
            generate_repeating_pulses(signals, nr_channels=24, reserved_channels=3, progress=False)

    def test_validation_masking_channels_must_exist_in_signals(self):
        """Test that masking channels must target channels present in signals."""
        signals = [Signal(frequency=10, channels=[0])]
        masking_signals = [Signal(frequency=10, channels=[1])]
        with pytest.raises(ValueError, match="Masking channels must be a subset"):
            generate_repeating_pulses(
                signals, masking_signals=masking_signals, progress=False
            )

    def test_custom_min_instruction_len(self):
        """Test with custom minimum instruction length."""
        signals = [Signal(frequency=10, channels=[0])]
        sequence = generate_repeating_pulses(
            signals, min_instruction_len=50, progress=False
        )
        assert isinstance(sequence, InstructionSequence)

    def test_branch_instruction_data(self):
        """Test that BRANCH instruction branches to start."""
        signals = [Signal(frequency=10, channels=[0])]
        sequence = generate_repeating_pulses(signals, progress=False)
        # Last instruction should branch to index 0
        assert sequence.instructions[-1].inst_data == 0

    def test_all_instructions_have_valid_flags(self):
        """Test that all instructions have 24 flags."""
        signals = [Signal(frequency=10, channels=[0])]
        sequence = generate_repeating_pulses(signals, progress=False)
        for instruction in sequence.instructions:
            assert len(instruction.flags) == 24

    def test_progress_bar_disabled(self):
        """Test that progress=False works without errors."""
        signals = [Signal(frequency=10, channels=[0])]
        # Should not raise any errors
        sequence = generate_repeating_pulses(signals, progress=False)
        assert isinstance(sequence, InstructionSequence)
