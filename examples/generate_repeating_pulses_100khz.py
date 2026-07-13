"""Compile the 23 Hz laser timing sequence with a continuous 100 kHz carrier."""

from pulseblaster import InstructionSequence, OptimizationLevel, Signal, generate_pulses


def build_signals() -> list[Signal]:
    trigger_offset_ms = 1
    qswitch_delay_us = 80
    frequency = 23

    return [
        Signal(
            frequency=frequency,
            offset=0,
            high=100_000,
            channels=[0, 7],
        ),
        Signal(
            frequency=frequency,
            offset=int(trigger_offset_ms * 1e6),
            high=100_000,
            channels=[1, 4],
        ),
        Signal(
            frequency=frequency,
            offset=int(trigger_offset_ms * 1e6 + qswitch_delay_us * 1e3),
            high=100_000,
            channels=[2, 5],
        ),
        Signal(
            frequency=frequency / 2,
            offset=int(1 / frequency * 1e9) - int(3e6),
            high=int(1 / frequency * 1e9),
            channels=[3, 6],
        ),
        Signal(
            frequency=100_000,
            duty_cycle=0.5,
            channels=[8],
        ),
    ]


def build_sequence() -> InstructionSequence:
    return generate_pulses.generate_repeating_pulses(
        build_signals(),
        optimization=OptimizationLevel.ADVANCED,
        progress=False,
    )


if __name__ == "__main__":
    sequence = build_sequence()
    print(sequence.compilation_report)
