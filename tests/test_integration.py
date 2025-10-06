"""Integration tests for pulseblaster package.

These tests verify that different components work together correctly.
"""

import pytest

from pulseblaster import (
    Signal,
    generate_pulses,
    plot_sequence,
    code_to_instructions,
)
from pulseblaster.data_structures import InstructionSequence, Opcode


class TestEndToEndWorkflow:
    """Test complete workflows from signal definition to instruction generation."""

    def test_simple_pulse_workflow(self):
        """Test creating a signal and generating pulse sequence."""
        # Create signal
        signal = Signal(frequency=10, channels=[0], duty_cycle=0.5)
        
        # Generate sequence
        sequence = generate_pulses.generate_repeating_pulses(
            [signal], progress=False
        )
        
        # Verify sequence
        assert isinstance(sequence, InstructionSequence)
        assert len(sequence.instructions) > 0
        assert sequence.instructions[-1].opcode == Opcode.BRANCH

    def test_multiple_signals_workflow(self):
        """Test workflow with multiple signals."""
        # Create multiple signals
        flashlamp = Signal(
            frequency=10, offset=0, high=int(1e6), channels=[1, 3]
        )
        qswitch = Signal(
            frequency=20, offset=int(90 * 1e3), high=int(1e6), channels=[2, 4]
        )
        
        # Generate sequence
        sequence = generate_pulses.generate_repeating_pulses(
            [flashlamp, qswitch], progress=False
        )
        
        # Verify
        assert isinstance(sequence, InstructionSequence)
        assert len(sequence.instructions) > 0

    def test_masking_workflow(self):
        """Test workflow with masking signals."""
        signal = Signal(frequency=10, channels=[0])
        masking = Signal(frequency=5, channels=[0])
        
        sequence = generate_pulses.generate_repeating_pulses(
            [signal], masking_signals=[masking], progress=False
        )
        
        assert isinstance(sequence, InstructionSequence)
        assert len(sequence.instructions) > 0

    def test_code_parsing_workflow(self):
        """Test parsing code and creating instruction sequence."""
        code = """
        start: 0x000001, 100ns, CONTINUE
               0x000000, 200ns, BRANCH, start
        """
        
        sequence = code_to_instructions(code)
        
        assert isinstance(sequence, InstructionSequence)
        assert len(sequence.instructions) == 2
        assert sequence.branch_index is not None

    def test_active_low_signal_workflow(self):
        """Test workflow with active low signals."""
        signal = Signal(frequency=15, channels=[5], active_high=False)
        
        sequence = generate_pulses.generate_repeating_pulses(
            [signal], progress=False
        )
        
        assert isinstance(sequence, InstructionSequence)
        # Check that flags are properly inverted for active low

    def test_offset_signals_workflow(self):
        """Test workflow with time-offset signals."""
        signal1 = Signal(frequency=10, channels=[0], offset=0)
        signal2 = Signal(frequency=10, channels=[1], offset=int(50e6))
        
        sequence = generate_pulses.generate_repeating_pulses(
            [signal1, signal2], progress=False
        )
        
        assert isinstance(sequence, InstructionSequence)
        assert len(sequence.instructions) > 0


class TestSequenceProperties:
    """Test that generated sequences have correct properties."""

    def test_sequence_has_valid_durations(self):
        """Test that all durations are positive."""
        signal = Signal(frequency=10, channels=[0])
        sequence = generate_pulses.generate_repeating_pulses(
            [signal], progress=False
        )
        
        for instruction in sequence.instructions:
            assert instruction.duration > 0

    def test_sequence_has_valid_flags(self):
        """Test that all flags arrays have correct length."""
        signal = Signal(frequency=10, channels=[0])
        sequence = generate_pulses.generate_repeating_pulses(
            [signal], progress=False
        )
        
        for instruction in sequence.instructions:
            assert len(instruction.flags) == 24
            assert all(f in [0, 1] for f in instruction.flags)

    def test_sequence_ends_with_branch(self):
        """Test that repeating sequences end with BRANCH."""
        signal = Signal(frequency=10, channels=[0])
        sequence = generate_pulses.generate_repeating_pulses(
            [signal], progress=False
        )
        
        assert sequence.instructions[-1].opcode == Opcode.BRANCH
        assert sequence.instructions[-1].inst_data == 0

    def test_unrolled_sequence_properties(self):
        """Test that unrolled sequence has valid properties."""
        signal = Signal(frequency=10, channels=[0])
        sequence = generate_pulses.generate_repeating_pulses(
            [signal], progress=False
        )
        
        # Check unrolled properties
        assert len(sequence.duration) > 0
        assert len(sequence.flags) > 0
        assert len(sequence.duration) == len(sequence.flags)


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_very_high_frequency(self):
        """Test with very high frequency signal."""
        signal = Signal(frequency=1000, channels=[0], duty_cycle=0.1)
        sequence = generate_pulses.generate_repeating_pulses(
            [signal], progress=False
        )
        assert isinstance(sequence, InstructionSequence)

    def test_very_low_frequency(self):
        """Test with very low frequency signal."""
        signal = Signal(frequency=1, channels=[0], duty_cycle=0.5)
        sequence = generate_pulses.generate_repeating_pulses(
            [signal], min_instruction_len=100, progress=False
        )
        assert isinstance(sequence, InstructionSequence)

    def test_many_channels(self):
        """Test with many channels."""
        signal = Signal(
            frequency=10,
            channels=list(range(20)),  # 20 channels
            duty_cycle=0.5
        )
        sequence = generate_pulses.generate_repeating_pulses(
            [signal], progress=False
        )
        assert isinstance(sequence, InstructionSequence)

    def test_small_duty_cycle(self):
        """Test with very small duty cycle."""
        signal = Signal(frequency=10, channels=[0], duty_cycle=0.01)
        sequence = generate_pulses.generate_repeating_pulses(
            [signal], progress=False
        )
        assert isinstance(sequence, InstructionSequence)

    def test_large_duty_cycle(self):
        """Test with large duty cycle."""
        signal = Signal(frequency=10, channels=[0], duty_cycle=0.99)
        sequence = generate_pulses.generate_repeating_pulses(
            [signal], progress=False
        )
        assert isinstance(sequence, InstructionSequence)


class TestErrorHandling:
    """Test error handling across components."""

    def test_invalid_signal_propagates(self):
        """Test that invalid signal creation raises appropriate errors."""
        with pytest.raises(ValueError):
            Signal(frequency=-10, channels=[0])

    def test_empty_signals_raises_error(self):
        """Test that generating with empty signals raises error."""
        with pytest.raises(ValueError):
            generate_pulses.generate_repeating_pulses([], progress=False)

    def test_invalid_code_raises_error(self):
        """Test that invalid code raises appropriate errors."""
        code = "invalid_format, 100ns, CONTINUE"
        with pytest.raises(ValueError):
            code_to_instructions(code)

    def test_conflicting_parameters(self):
        """Test that conflicting signal parameters are handled."""
        # This should work - high time should override duty cycle
        # frequency=10 Hz means period = 100ms = 1e8 ns
        # Using a high time less than the period
        signal = Signal(
            frequency=10,
            channels=[0],
            high=int(5e7),  # 50ms (half of 100ms period)
            duty_cycle=0.5
        )
        # Duty cycle should be recalculated based on high
        assert signal._duty_cycle_set is False
        # Should be 0.5 since high is 50ms and period is 100ms
        assert abs(signal.duty_cycle - 0.5) < 0.01


class TestCompatibility:
    """Test compatibility between different components."""

    def test_code_and_generate_produce_same_structure(self):
        """Test that manually generated and parsed code produce similar structures."""
        # Generate from signal
        signal = Signal(frequency=10, channels=[0])
        generated_seq = generate_pulses.generate_repeating_pulses(
            [signal], progress=False
        )
        
        # Parse from code
        code = "0x000001, 100ns, BRANCH, 0"
        parsed_seq = code_to_instructions(code)
        
        # Both should be InstructionSequence
        assert isinstance(generated_seq, InstructionSequence)
        assert isinstance(parsed_seq, InstructionSequence)
        
        # Both should have valid properties
        assert len(generated_seq.instructions) > 0
        assert len(parsed_seq.instructions) > 0

    def test_instruction_sequence_consistency(self):
        """Test that InstructionSequence maintains consistency."""
        signal = Signal(frequency=10, channels=[0])
        sequence = generate_pulses.generate_repeating_pulses(
            [signal], progress=False
        )
        
        # Unrolled length should match or exceed instruction count
        assert len(sequence.duration) >= len(sequence.instructions)
