"""Tests for utils module."""

import pytest

from pulseblaster.data_structures import Signal
from pulseblaster.utils import all_channels_off, round_to_nearest_n_ns


class TestRoundToNearestNNs:
    """Tests for round_to_nearest_n_ns function."""

    def test_exact_multiple(self):
        """Test rounding when value is already an exact multiple."""
        result = round_to_nearest_n_ns(1000, 100)
        assert result == 1000

    def test_round_down(self):
        """Test rounding down to nearest multiple."""
        result = round_to_nearest_n_ns(1024, 100)
        assert result == 1000

    def test_round_up(self):
        """Test rounding up to nearest multiple."""
        result = round_to_nearest_n_ns(1076, 100)
        assert result == 1100

    def test_round_halfway(self):
        """Test rounding at halfway point (should round to nearest even)."""
        result = round_to_nearest_n_ns(1050, 100)
        assert result == 1000 or result == 1100  # Python rounds to nearest even

    def test_zero_value(self):
        """Test rounding zero."""
        result = round_to_nearest_n_ns(0, 100)
        assert result == 0

    def test_small_value(self):
        """Test rounding small values."""
        result = round_to_nearest_n_ns(13, 5)
        assert result == 15

    def test_large_value(self):
        """Test rounding large values."""
        result = round_to_nearest_n_ns(1234567, 1000)
        assert result == 1235000

    def test_ns_round_1(self):
        """Test with ns_round = 1 (should return original value)."""
        result = round_to_nearest_n_ns(12345, 1)
        assert result == 12345

    def test_various_multiples(self):
        """Test rounding to various multiples."""
        assert round_to_nearest_n_ns(103, 20) == 100
        assert round_to_nearest_n_ns(108, 20) == 100 or round_to_nearest_n_ns(108, 20) == 120
        assert round_to_nearest_n_ns(112, 20) == 120
        assert round_to_nearest_n_ns(250, 50) == 250


class TestAllChannelsOff:
    """Tests for all_channels_off function."""

    def test_all_active_high(self):
        """Test with all signals active high."""
        signals = [
            Signal(frequency=10, channels=[0, 1], active_high=True),
            Signal(frequency=20, channels=[2], active_high=True),
        ]
        result = all_channels_off(signals)
        assert len(result) == 24
        assert result == [0] * 24

    def test_all_active_low(self):
        """Test with all signals active low."""
        signals = [
            Signal(frequency=10, channels=[0, 1], active_high=False),
            Signal(frequency=20, channels=[2], active_high=False),
        ]
        result = all_channels_off(signals)
        assert len(result) == 24
        assert result[0] == 1
        assert result[1] == 1
        assert result[2] == 1
        # Last 3 channels should be high when any active_low signals present
        assert result[-3:] == [1, 1, 1]

    def test_mixed_active_high_low(self):
        """Test with mixed active high and low signals."""
        signals = [
            Signal(frequency=10, channels=[0], active_high=True),
            Signal(frequency=20, channels=[1, 2], active_high=False),
        ]
        result = all_channels_off(signals)
        assert result[0] == 0  # active high, so off = 0
        assert result[1] == 1  # active low, so off = 1
        assert result[2] == 1  # active low, so off = 1
        assert result[-3:] == [1, 1, 1]  # Last 3 should be high

    def test_single_signal_active_low(self):
        """Test with single active low signal."""
        signals = [
            Signal(frequency=15, channels=[5], active_high=False),
        ]
        result = all_channels_off(signals)
        assert result[5] == 1
        assert result[-3:] == [1, 1, 1]

    def test_empty_signals_list(self):
        """Test with empty signals list."""
        result = all_channels_off([])
        assert result == [0] * 24

    def test_multiple_channels_per_signal(self):
        """Test signals with multiple channels."""
        signals = [
            Signal(frequency=10, channels=[0, 1, 2, 3], active_high=False),
        ]
        result = all_channels_off(signals)
        assert result[0] == 1
        assert result[1] == 1
        assert result[2] == 1
        assert result[3] == 1
        assert result[4] == 0
        assert result[-3:] == [1, 1, 1]

    def test_overlapping_channels(self):
        """Test with overlapping channels in different signals."""
        signals = [
            Signal(frequency=10, channels=[0, 1], active_high=False),
            Signal(frequency=20, channels=[1, 2], active_high=False),
        ]
        result = all_channels_off(signals)
        # Channel 1 is in both, but should still be 1
        assert result[0] == 1
        assert result[1] == 1
        assert result[2] == 1
        assert result[-3:] == [1, 1, 1]

    def test_high_channel_numbers(self):
        """Test with high channel numbers (near 23)."""
        signals = [
            Signal(frequency=10, channels=[20], active_high=False),
        ]
        result = all_channels_off(signals)
        assert result[20] == 1
        assert result[-3:] == [1, 1, 1]

    def test_custom_channel_count_with_no_reserved_channels(self):
        """Test custom channel count without reserved channels."""
        signals = [Signal(frequency=10, channels=[10], active_high=False)]
        result = all_channels_off(signals, nr_channels=12, reserved_channels=0)
        assert len(result) == 12
        assert result[10] == 1

    def test_reserved_channels_must_be_smaller_than_total_channels(self):
        """Test invalid reserved channel configuration raises ValueError."""
        with pytest.raises(ValueError, match="reserved_channels"):
            all_channels_off([], nr_channels=4, reserved_channels=4)
