"""Tests for generate_pulses module."""

import random

import numpy as np
import pytest

from pulseblaster.data_structures import InstructionSequence, Opcode, Pulse, Signal
from pulseblaster.generate_pulses import (
    WaveformInterval,
    _compress_intervals,
    _reference_intervals,
    calculate_resets,
    calculate_resets_masking,
    generate_repeating_pulses,
    minimum_duration_and_num_cycles,
    pulses_convert_to_instruction_length,
    rescale_pulses,
)
from pulseblaster.validation import ESR_PRO_250, BoardProfile, decode_control_mode


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

    def test_short_period_is_not_rounded_to_zero(self):
        """A period shorter than the coarse rounding grid remains nonzero."""
        signals = [
            Signal(frequency=23, channels=[0]),
            Signal(frequency=11.5, channels=[1]),
            Signal(frequency=100_000, channels=[2]),
        ]

        duration, nr_cycles, frequencies = minimum_duration_and_num_cycles(
            signals,
            [],
            None,
            ESR_PRO_250.min_instruction_len_ns,
            t_max=int(1e12),
        )

        assert duration > 0
        assert nr_cycles > 0
        assert frequencies[-1] > 0
        assert frequencies[-1] == pytest.approx(100_000, rel=0.002)


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

    def test_validation_invalid_profile_clock(self):
        """Test that invalid profile clock raises ValueError."""
        with pytest.raises(ValueError, match="clock_mhz must be positive"):
            BoardProfile(clock_mhz=0)

    def test_validation_signal_channel_exceeds_controllable_range(self):
        """Test that requested channels fit in configured controllable channel range."""
        signals = [Signal(frequency=10, channels=[21])]
        with pytest.raises(ValueError, match="controllable range"):
            generate_repeating_pulses(signals, progress=False)

    def test_validation_masking_channels_must_exist_in_signals(self):
        """Test that masking channels must target channels present in signals."""
        signals = [Signal(frequency=10, channels=[0])]
        masking_signals = [Signal(frequency=10, channels=[1])]
        with pytest.raises(ValueError, match="Masking channels must be a subset"):
            generate_repeating_pulses(
                signals, masking_signals=masking_signals, progress=False
            )

    def test_custom_profile_min_instruction_len(self):
        """Test with custom profile minimum instruction length."""
        signals = [Signal(frequency=10, channels=[0])]
        profile = BoardProfile(min_instruction_cycles=25)
        sequence = generate_repeating_pulses(
            signals, profile=profile, progress=False
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

    def test_generated_normal_instructions_use_on_disable_mode(self):
        """Test generated normal instructions set ESR-PRO control bits to ON/111."""
        signals = [Signal(frequency=10, channels=[0])]
        sequence = generate_repeating_pulses(signals, progress=False)
        for instruction in sequence.instructions:
            assert decode_control_mode(instruction.flags, ESR_PRO_250) == 7

    def test_channel_20_is_valid(self):
        """Test that the last ESR-PRO user output channel is accepted."""
        signals = [Signal(frequency=10, channels=[20])]
        sequence = generate_repeating_pulses(signals, progress=False)
        assert isinstance(sequence, InstructionSequence)

    def test_generated_durations_are_profile_quantum_multiples(self):
        """Test generated durations align to the ESR_PRO_250 24 ns quantum."""
        signals = [Signal(frequency=10, channels=[0])]
        sequence = generate_repeating_pulses(signals, progress=False)
        assert all(
            instruction.duration % ESR_PRO_250.min_instruction_len_ns == 0
            for instruction in sequence.instructions
        )

    def test_progress_bar_disabled(self):
        """Test that progress=False works without errors."""
        signals = [Signal(frequency=10, channels=[0])]
        # Should not raise any errors
        sequence = generate_repeating_pulses(signals, progress=False)
        assert isinstance(sequence, InstructionSequence)

    def test_100_khz_carrier_is_compressed_with_loops(self):
        frequency = 23
        signals = [
            Signal(
                frequency=frequency,
                offset=0,
                high=100_000,
                channels=[0, 7],
            ),
            Signal(
                frequency=frequency,
                offset=1_000_000,
                high=100_000,
                channels=[1, 4],
            ),
            Signal(
                frequency=frequency,
                offset=1_080_000,
                high=100_000,
                channels=[2, 5],
            ),
            Signal(
                frequency=frequency / 2,
                offset=int(1 / frequency * 1e9) - int(3e6),
                high=int(1 / frequency * 1e9),
                channels=[3, 6],
            ),
            Signal(frequency=100_000, duty_cycle=0.5, channels=[8]),
        ]

        reference, duration_ticks = _reference_intervals(signals, [], ESR_PRO_250)
        sequence = generate_repeating_pulses(signals, progress=False)

        assert duration_ticks * ESR_PRO_250.clock_period_ns == 2_000_000_000
        assert len(reference) == 400_308
        assert len(sequence.instructions) < ESR_PRO_250.max_program_instructions
        assert any(
            instruction.opcode == Opcode.LOOP
            for instruction in sequence.instructions
        )
        assert int(sequence.duration.sum()) == 2_000_000_000

        reference_duration = np.asarray(
            [item.duration_ticks * ESR_PRO_250.clock_period_ns for item in reference]
        )
        reference_flags = np.asarray([item.flags for item in reference])
        assert np.array_equal(sequence.duration, reference_duration)
        assert np.array_equal(sequence.flags, reference_flags)

        carrier = sequence.flags[:, 8]
        trigger = sequence.flags[:, 0]
        assert np.count_nonzero((carrier == 1) & (np.roll(carrier, 1) == 0)) == 200_000
        assert np.count_nonzero((trigger == 1) & (np.roll(trigger, 1) == 0)) == 46

    def test_timing_collision_reports_clear_error(self):
        signals = [
            Signal(frequency=100_000, duty_cycle=0.5, channels=[0]),
            Signal(frequency=1, offset=5_004, high=100, channels=[1]),
        ]

        with pytest.raises(ValueError, match="Timing collision"):
            generate_repeating_pulses(signals, progress=False)

    def test_superperiod_limit_is_configurable(self):
        signals = [
            Signal(frequency=10, channels=[0]),
            Signal(frequency=15, channels=[1]),
        ]

        with pytest.raises(ValueError, match="Common timespan"):
            generate_repeating_pulses(
                signals, progress=False, max_superperiod_ns=100_000_000
            )

    def test_loop_counts_are_split_at_profile_limit(self):
        profile = BoardProfile(max_loop_iterations=3)
        high_flags = tuple([1, *([0] * 23)])
        low_flags = tuple([0] * 24)
        pattern = [
            WaveformInterval(high_flags, 100),
            WaveformInterval(low_flags, 100),
        ]
        intervals = pattern * 8 + [WaveformInterval(low_flags, 100)]

        instructions = _compress_intervals(intervals, profile)

        assert [
            instruction.inst_data
            for instruction in instructions
            if instruction.opcode == Opcode.LOOP
        ] == [3, 3, 2]

    def test_masking_signal_gates_only_its_high_window(self):
        signal = Signal(frequency=10, duty_cycle=0.5, channels=[0])
        mask = Signal(frequency=20, duty_cycle=0.25, channels=[0])

        sequence = generate_repeating_pulses(
            [signal], masking_signals=[mask], progress=False
        )

        high_duration = int(sequence.duration[sequence.flags[:, 0] == 1].sum())
        assert high_duration == 12_500_000

    def test_overlapping_signals_on_a_channel_are_combined_with_or(self):
        signals = [
            Signal(frequency=10, duty_cycle=0.5, channels=[0]),
            Signal(frequency=20, duty_cycle=0.5, channels=[0]),
        ]

        sequence = generate_repeating_pulses(signals, progress=False)

        high_duration = int(sequence.duration[sequence.flags[:, 0] == 1].sum())
        assert high_duration == 75_000_000

    def test_active_low_signal_is_physically_inverted(self):
        signal = Signal(
            frequency=10, duty_cycle=0.5, channels=[0], active_high=False
        )

        sequence = generate_repeating_pulses([signal], progress=False)

        assert sequence.flags[0, 0] == 0
        assert sequence.flags[-1, 0] == 1
        high_duration = int(sequence.duration[sequence.flags[:, 0] == 1].sum())
        assert high_duration == 50_000_000

    @pytest.mark.parametrize("seed", range(5))
    def test_loop_compression_matches_reference_for_generated_cases(self, seed):
        rng = random.Random(seed)
        signals = [
            Signal(
                frequency=rng.choice([10, 20, 50, 100]),
                duty_cycle=rng.choice([0.25, 0.5, 0.75]),
                channels=[channel],
                active_high=rng.choice([True, False]),
            )
            for channel in range(4)
        ]

        reference, _ = _reference_intervals(signals, [], ESR_PRO_250)
        sequence = generate_repeating_pulses(signals, progress=False)
        reference_duration = np.asarray(
            [item.duration_ticks * ESR_PRO_250.clock_period_ns for item in reference]
        )
        reference_flags = np.asarray([item.flags for item in reference])

        assert np.array_equal(sequence.duration, reference_duration)
        assert np.array_equal(sequence.flags, reference_flags)
