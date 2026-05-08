import logging

import matplotlib.pyplot as plt

from pulseblaster import Signal, generate_pulses, plot_sequence

FORMAT = "%(message)s"
logging.basicConfig(level=logging.INFO, format=FORMAT, datefmt="[%X]")

# 50 Hz signal to be output on channels 1&3, which is high for 1 ms
flashlamp = Signal(
    frequency=50, offset=0, high=int(1e6), channels=[1, 3], active_high=True
)

# 50 Hz signal to be output on channels 2&4, which is offset by 90 us w.r.t to the
# previous signal, high for 1 ms
qswitch = Signal(
    frequency=50,
    offset=int(90 * 1e3),
    high=int(1e6),
    channels=[2, 4],
    active_high=True,
)

# 500 Hz signal to be output on channel 5, offset by 110 us
other = Signal(
    frequency=500,
    offset=int(110 * 1e3),
    channels=[5],
    active_high=True,
)

# generate an infinitely repeating pulse sequence
sequence = generate_pulses.generate_repeating_pulses(
    [flashlamp, qswitch, other],
    masking_signals=[],
    progress=True,
)

# plot the pulse sequence
plot_sequence(sequence)
plt.show()
