import matplotlib.pyplot as plt

from pulseblaster import code_to_instructions, plot_sequence

code = """// Sample program for SpinCore PulseBlaster Interpreter.
// SOS using sub routines.
       0x000000, 50ms, CONTINUE
start: 0x000000, 1ms, JSR, short
       0x000000, 1ms, JSR, long
       0x000000, 1ms, JSR, short
       0x000000, 50ms, BRANCH, start

// 3 Short
short: 0xFFFFFF, 10ms
       0x000000, 10ms
       0xFFFFFF, 10ms
       0x000000, 10ms
       0xFFFFFF, 10ms
       0x000000, 10ms, RTS

// 3 Long
long:  0xFFFFFF, 30ms
       0x000000, 10ms
       0xFFFFFF, 30ms
       0x000000, 10ms
       0xFFFFFF, 30ms
       0x000000, 10ms, RTS
"""

code = """// Sample program for SpinCore PulseBlaster Interpreter.
// SOS using loops.

// 3 Short
       0x000000, 500ms, CONTINUE
start: 0xFFFFFF, 100ms, LOOP, 3
       0x000000, 100ms, END_LOOP

// 3 Long
       0xFFFFFF, 300ms, LOOP, 3
       0x000000, 100ms, END_LOOP

// 3 Short
       0xFFFFFF, 100ms, LOOP, 3
       0x000000, 100ms, END_LOOP

// A pause
       0x000000, 500ms, branch, start // branch to start
"""

sequence = code_to_instructions(code)

plot_sequence(sequence, offset=1.5)

plt.show()
