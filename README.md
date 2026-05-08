# PulseBlaster

Python interface for a Spincore PulseBlaster pulse generator.  
Only tested with the PulseBlaster ESR-PRO USB 250 MHz.



## Examples

### Generating a pulse sequence
With `generate_repeating_pulses` a series of repeating pulses can be generated composed of `Signal`.
Each `Signal` represents a pulse sequence with a frequency, offset and pulse high duration and channel(s). `generate_repeating_pulses` takes the frequency, offset and pulse high duration into account to find the minimum viable repeating sequence that corresponds to all the required signals.

The default `ESR_PRO_250` profile models a 250 MHz PulseBlasterESR-PRO:
24 flag bits, 21 user output bits (`0..20`), and short-pulse control bits `21..23`.
Generated normal instructions set the control bits to `ON` (`111`) to disable
short-pulse gating.

`masking_signals` act as periodic gating signals: their channels must be a subset of the
base `signals` channels, and they enable the associated base channels only during the
mask pulse high window.

```python
import matplotlib.pyplot as plt
from pulseblaster import Signal, generate_pulses, plot_sequence

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
    [flashlamp, qswitch, other], masking_signals=None, progress=False
)

# plot the pulse sequence
plot_sequence(sequence)
plt.show()

```
![](images/sequence.png)

### Sequence validation
Generated and parsed sequences are validated before use.
Validation checks include:
- instruction structure (duration, opcode, inst_data, flag width)
- loop/branch target consistency
- mandatory clock-cycle alignment and short-pulse control-bit rules

Unlike raw SpinAPI, which rounds instruction durations to the nearest clock cycle, this
package validates durations strictly. Custom or parsed instructions must already be
aligned to the selected `BoardProfile` clock. For `ESR_PRO_250`, durations must be
multiples of 4 ns and at least 24 ns.

You can also validate custom instruction lists directly:

```python
from pulseblaster import ESR_PRO_250, validate_sequence

validate_sequence(sequence.instructions, profile=ESR_PRO_250)
```

### Programming
The code below shows how to program the PulseBlaster with a sequence and starts the sequence.

```python
from pulseblaster import PulseBlaster

# initialize the connection
pulse_gen = PulseBlaster(board_number = 0)

# program the sequence on the device
pulse_gen.program(sequence = sequence.instructions)

# start the sequence
pulse_gen.start()
```

### Reading PulseBlaster Interpreter code
PulseBlaster Interpreter code can be read, converted into a sequence of instructions and plotted: 
```Python
import matplotlib.pyplot as plt

from pulseblaster import code_to_instructions, plot_sequence

code = """// Sample program for SpinCore PulseBlaster Interpreter.
// SOS using loops.

// 3 Short
       0xE00000, 500ms, CONTINUE
start: 0xFFFFFF, 100ms, LOOP, 3
       0xE00000, 100ms, END_LOOP

// 3 Long
       0xFFFFFF, 300ms, LOOP, 3
       0xE00000, 100ms, END_LOOP

// 3 Short
       0xFFFFFF, 100ms, LOOP, 3
       0xE00000, 100ms, END_LOOP

// A pause
       0xE00000, 500ms, branch, start // branch to start
"""

sequence = code_to_instructions(code)

plot_sequence(sequence)

plt.show()
```
![](images/read_code.png)
