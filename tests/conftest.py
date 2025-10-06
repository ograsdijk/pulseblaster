"""Pytest configuration and fixtures for pulseblaster tests."""

import pytest

from pulseblaster.data_structures import Signal


@pytest.fixture
def simple_signal():
    """Create a simple signal for testing."""
    return Signal(frequency=10, channels=[0], duty_cycle=0.5)


@pytest.fixture
def multiple_signals():
    """Create multiple signals for testing."""
    return [
        Signal(frequency=10, channels=[0], duty_cycle=0.5),
        Signal(frequency=20, channels=[1], duty_cycle=0.3),
        Signal(frequency=50, channels=[2, 3], offset=100000),
    ]


@pytest.fixture
def active_low_signal():
    """Create an active low signal for testing."""
    return Signal(frequency=15, channels=[5], active_high=False)


@pytest.fixture
def signal_with_offset():
    """Create a signal with offset for testing."""
    return Signal(frequency=25, channels=[4], offset=500000, duty_cycle=0.4)


@pytest.fixture
def masking_signal():
    """Create a masking signal for testing."""
    return Signal(frequency=5, channels=[0], duty_cycle=0.6)


@pytest.fixture
def sample_pulseblaster_code():
    """Sample PulseBlaster assembly code for testing."""
    return """
    // Simple test program
    start: 0x000001, 100ns, CONTINUE
           0x000000, 200ns, CONTINUE
           0x000001, 150ns, BRANCH, start
    """


@pytest.fixture
def loop_code():
    """PulseBlaster code with loops for testing."""
    return """
    0x000001, 100ns, LOOP, 5
    0x000000, 200ns, END_LOOP
    0x000001, 300ns, STOP
    """


@pytest.fixture
def jsr_code():
    """PulseBlaster code with JSR/RTS for testing."""
    return """
    0x000001, 100ns, JSR, sub
    0x000000, 200ns, STOP
    sub: 0x000001, 50ns, CONTINUE
    0x000000, 50ns, RTS
    """
