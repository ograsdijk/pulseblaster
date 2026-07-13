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

The generator schedules transitions on the 4 ns hardware clock and uses rational
frequency analysis to find the complete repeating superperiod. Non-integer-clock
periods are phase-accumulated across that superperiod, preserving the requested
average frequency with at most one clock tick of individual-period variation.

Repeated output blocks are compiled to hardware `LOOP`/`END_LOOP` instructions.
This is particularly useful when a fast carrier runs while slower channels remain
stationary: the carrier pattern is stored once for each slow-channel state instead of
being expanded into every edge. The default profile enforces the 4096-word program
memory and eight-level loop limit.

The compiler can target consecutive repeated blocks, `LONG_DELAY`, nested-loop
factoring, and `JSR`/`RTS` deduplication through the advanced policy.
Basic optimization remains the default until advanced programs have broader physical
hardware coverage.

```python
from pulseblaster import OptimizationLevel

sequence = generate_pulses.generate_repeating_pulses(
    signals,
    optimization=OptimizationLevel.ADVANCED,
    progress=False,
)

print(sequence.compilation_report)
```

The policies are:

- `NONE`: emit event intervals directly, using `LONG_DELAY` only when required for
  hardware legality.
- `BASIC`: compress adjacent repeated blocks with one-level hardware loops.
- `ADVANCED`: add bounded dynamic-programming windows, nested-loop factoring,
  non-adjacent subroutine reuse, and choose the lowest-cost legal candidate.

`CompilationReport` records the superperiod, reference and stored instruction counts,
compression ratio, compile time, loop depth, long-delay use, and subroutine use.

Transition streams are merged lazily, keeping event scheduling memory proportional to
the number of input signals rather than the number of raw clock edges. The normalized
reference intervals are retained while the global optimizer compares candidates and
their waveforms are verified against the selected instruction program as a stream.

The complete 23 Hz laser sequence with a continuous 100 kHz output is in
`examples/generate_repeating_pulses_100khz.py`. On the default 250 MHz profile it
currently compiles a 2 s superperiod with 400,308 executed intervals into 1,558 stored
instructions, below the 4,096-instruction limit.

### Physical-board validation

Compilation and simulator tests do not establish firmware-specific electrical timing.
Before connecting the laser, validate the generated program with a scope or logic
analyzer and a disconnected or otherwise safe load:

1. Confirm the installed board is the 250 MHz, 24-8 design represented by
   `ESR_PRO_250`, including its 4,096-word memory and instruction timing minima.
2. Run `python examples/generate_repeating_pulses_100khz.py` and confirm the report
   stays at or below 4,096 stored instructions and has a 2,000,000,000 ns superperiod.
3. Program the board without enabling the laser. Check channel 8 first: 5 us high,
   5 us low, continuously, including across every slow-channel transition.
4. Check the paired 23 Hz trigger, flashlamp, and Q-switch channels. Confirm the
   flashlamp begins at 1 ms and the Q-switch at 1.080 ms relative to the trigger.
5. Check the 11.5 Hz shutter waveform and the transition at the 2 s branch boundary.
   There must be no extra or missing 100 kHz edge at the branch.
6. Only after those checks pass, reconnect the controlled equipment and repeat at a
   safe operating level before normal use.

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
multiples of 4 ns. The firmware minimum is opcode-specific: `LOOP`, `END_LOOP`, and
`BRANCH` can use the five-cycle (20 ns) base minimum, while `CONTINUE`, `JSR`, `RTS`,
`LONG_DELAY`, `WAIT`, and `STOP` require seven cycles (28 ns).

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
