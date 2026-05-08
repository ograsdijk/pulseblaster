# SpinAPI (PulseBlaster) quick guide

This is a practical overview of how SpinAPI programs a SpinCore PulseBlaster-style board (including PulseBlasterESR-PRO). It focuses on how a pulse program is built, how a single instruction maps to hardware fields, and how timing and flow control work.

## Core idea

A pulse program is a list of *instructions*. Each instruction drives the output pins to a chosen pattern for a chosen duration, plus optional flow control (loop, branch, wait, stop, subroutine).

At the hardware level, each instruction word contains these fields:

- **24-bit Output/Control**: the digital output pattern plus (on some firmwares) special control bits
- **20-bit Data**: the parameter for the opcode (example: loop count, branch address)
- **4-bit OpCode**: the instruction type (continue, stop, loop, branch, wait, etc.)
- **32-bit Delay Count**: how long this instruction lasts, in clock cycles

In SpinAPI you do not normally pack these bits yourself. You call `pb_inst(...)` and SpinAPI fills them.

## Typical SpinAPI program flow

Minimal structure:

1. Initialize the driver
2. Set the core clock (important for correct timing)
3. Start programming
4. Emit instructions (each call to `pb_inst` appends one instruction)
5. Stop programming
6. Start the program

```c
#include <stdio.h>
#include "spinapi.h"

int main(void) {
    if (pb_init() != 0) {
        fprintf(stderr, "pb_init failed: %s\n", pb_get_error());
        return 1;
    }

    pb_core_clock(250.0);  // MHz, adjust to your hardware

    pb_start_programming(PULSE_PROGRAM);

    // instruction list goes here

    pb_stop_programming();

    pb_start();  // run the loaded program

    // pb_close() can be called even while the pulse program continues to run
    pb_close();
    return 0;
}
```

### What `pb_inst` means

Canonical signature:

```c
int pb_inst(int flags, int inst, int inst_data, double length);
```

- `flags`: the output bitmask for that instruction (plus optional special bits)
- `inst`: opcode, such as `CONTINUE`, `STOP`, `LOOP`, `END_LOOP`, `BRANCH`, `WAIT`, `LONG_DELAY`, `JSR`, `RTS`
- `inst_data`: opcode parameter (example: loop count for `LOOP`, destination address for `BRANCH`)
- `length`: instruction duration in seconds (SpinAPI converts to clock cycles using `pb_core_clock`)

`pb_inst` returns the instruction address (0, 1, 2, ...) which you can save for later use (example: to branch back).

## Single-instruction examples

Assume a 250 MHz clock:

- Clock period is **4 ns**
- Resolution is **4 ns**
- Any duration becomes an integer number of cycles, `cycles = round(duration / 4 ns)` (rounding details depend on driver)

### 1) Simple output for a duration

Turn on outputs 0, 2, and 3 for 40 ns, then fall through to the next instruction.

```c
pb_inst(ON | 0x00000D, CONTINUE, 0, 40.0 * ns);
```

Notes:

- `0x00000D` is binary `...1101`, so bits 0, 2, 3 are high.
- `ON` is commonly used as a safe default to disable short-pulse gating, if your firmware implements it.

At 250 MHz, 40 ns is 10 cycles.

### 2) Two instructions: square wave

```c
pb_inst(ON | 0x000001, CONTINUE, 0, 100.0 * ns);  // output 0 high
pb_inst(ON | 0x000000, CONTINUE, 0, 100.0 * ns);  // output 0 low
```

Put these in a loop to repeat, shown below.

## Control flow

### Loops

A `LOOP` instruction starts a loop, and `END_LOOP` closes it.

Typical pattern:

```c
int start = pb_inst(ON | 0x000001, LOOP, 1000, 100.0 * ns);   // loop count in inst_data
pb_inst(ON | 0x000000, CONTINUE, 0,   100.0 * ns);
pb_inst(ON | 0x000000, END_LOOP, start, 100.0 * ns);         // start address in inst_data
```

This repeats the loop body 1000 times.

### Branch

Unconditional jump to an absolute instruction address:

```c
int top = pb_inst(ON | 0x000001, CONTINUE, 0, 100.0 * ns);
pb_inst(ON | 0x000000, CONTINUE, 0, 100.0 * ns);
pb_inst(ON | 0x000000, BRANCH, top, 100.0 * ns);
```

### Wait

`WAIT` pauses until a trigger condition is met (hardware trigger line or software trigger, depending on board and wiring). After the trigger, execution continues.

Common pattern:

```c
pb_inst(ON | 0x000000, WAIT, 0, 100.0 * ns);      // waits here
pb_inst(ON | 0x000001, CONTINUE, 0, 40.0 * ns);   // then pulse
```

Many firmwares have two practical constraints:

- `WAIT` is not allowed as the first instruction in the program.
- `WAIT` adds a small fixed latency (documented as several clock cycles on many boards).

### Stop

Stops execution:

```c
pb_inst(ON | 0x000000, STOP, 0, 100.0 * ns);
```

In many setups you will need a reset before a new trigger can restart from the top.

### Subroutines (JSR/RTS)

Useful for repeating a block from multiple places. Depth is limited (commonly 8).

```c
int sub = /* address of subroutine start */;

pb_inst(ON | 0x000000, JSR, sub, 100.0 * ns); // jump to subroutine
// ...
pb_inst(ON | 0x000000, RTS, 0, 100.0 * ns);   // return
```

## Long delays

Each instruction has a maximum single-instruction duration. For long waits, use `LONG_DELAY` or combine loops plus smaller delays.

Example:

```c
pb_inst(ON | 0x000000, LONG_DELAY, 1000, 1.0 * ms);  // effective delay is multiplier times length
```

Exact meaning of the multiplier and the allowed ranges are board and firmware dependent, but the pattern is standard: you trade one instruction for a much longer effective time.

## Short pulses (1–5 clock cycles)

Many PulseBlaster firmwares provide a feature where special control bits can force an output pattern to be asserted for only 1 to 5 clock cycles inside a longer instruction.

Typical macros:

- `ONE_PERIOD`, `TWO_PERIOD`, `THREE_PERIOD`, `FOUR_PERIOD`, `FIVE_PERIOD`
- `ON` commonly disables the feature

At 250 MHz, these widths are:

- `ONE_PERIOD` = 4 ns
- `TWO_PERIOD` = 8 ns
- `THREE_PERIOD` = 12 ns
- `FOUR_PERIOD` = 16 ns
- `FIVE_PERIOD` = 20 ns

Example: a 12 ns wide pulse inside a 40 ns instruction:

```c
pb_inst(THREE_PERIOD | 0x00000D, CONTINUE, 0, 40.0 * ns);
```

If you do not need short pulses, prefer `ON | pattern` on every instruction so you do not accidentally gate your outputs.

## Timing checklist for a 250 MHz system

- Base resolution: 4 ns
- Design durations as multiples of 4 ns when possible
- Some firmwares enforce a minimum instruction length in clock cycles (commonly 6 or 7 cycles). At 250 MHz that is 24 ns or 28 ns.
- If you need an effective 4–20 ns pulse and your minimum instruction length is longer than that, use short-pulse gating inside a longer instruction.

## Debugging tips

- If `pb_inst` returns a negative value, print `pb_get_error()` to see what parameter was invalid.
- If your timing seems off by a constant factor, verify you set `pb_core_clock` to the true hardware clock.
- If outputs appear truncated, make sure short-pulse gating is disabled by using `ON | pattern`.
- If `WAIT` does not resume, confirm your trigger polarity and wiring (many trigger inputs are pulled high and trigger on a low pulse).

## Mini template: triggered pulse burst

This example waits for a trigger, then emits a burst of 100 pulses on output 0 at 100 ns high, 100 ns low, then stops.

```c
pb_start_programming(PULSE_PROGRAM);

pb_inst(ON | 0x000000, CONTINUE, 0, 100.0 * ns);      // safety, not WAIT first
pb_inst(ON | 0x000000, WAIT,     0, 100.0 * ns);      // wait for trigger

int start = pb_inst(ON | 0x000001, LOOP, 100, 100.0 * ns);  // high
pb_inst(ON | 0x000000, CONTINUE, 0,   100.0 * ns);          // low
pb_inst(ON | 0x000000, END_LOOP, start, 100.0 * ns);

pb_inst(ON | 0x000000, STOP, 0, 100.0 * ns);

pb_stop_programming();
```

If you want, paste a pulse diagram (channels, pulse widths, repetition, trigger behavior) and I can convert it into an instruction list that respects your 250 MHz clock and likely minimum-instruction constraints.
