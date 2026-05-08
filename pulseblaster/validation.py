"""Validation helpers and board profiles for PulseBlaster instruction sequences."""

from collections.abc import Sequence
from dataclasses import dataclass, field

from .data_structures import Instruction, Opcode


@dataclass(frozen=True)
class BoardProfile:
    """Hardware and firmware constraints used to validate and emit instructions."""

    name: str = "PulseBlasterESR-PRO 250 MHz"
    clock_mhz: float = 250.0
    flag_bits: int = 24
    output_bits: int = 21
    control_bits: tuple[int, int, int] = (21, 22, 23)
    min_instruction_cycles: int = 6
    max_short_cycles: int = 5
    short_pulse_disable_modes: frozenset[int] = field(default_factory=lambda: frozenset({0, 7}))
    generated_disable_mode: int = 7
    wait_not_first: bool = True
    max_inst_data: int = (1 << 20) - 1
    max_delay_cycles: int = (1 << 32) - 1
    max_unrolled_instructions: int = 1_000_000

    def __post_init__(self) -> None:
        if self.flag_bits <= 0:
            raise ValueError(f"flag_bits must be positive, got {self.flag_bits}")
        if self.output_bits <= 0:
            raise ValueError(f"output_bits must be positive, got {self.output_bits}")
        if self.output_bits > self.flag_bits:
            raise ValueError(
                f"output_bits ({self.output_bits}) cannot exceed flag_bits ({self.flag_bits})"
            )
        if self.clock_mhz <= 0:
            raise ValueError(f"clock_mhz must be positive, got {self.clock_mhz}")
        if self.min_instruction_cycles <= 0:
            raise ValueError(
                "min_instruction_cycles must be positive, "
                f"got {self.min_instruction_cycles}"
            )
        if self.max_short_cycles < 0:
            raise ValueError(
                f"max_short_cycles must be non-negative, got {self.max_short_cycles}"
            )
        if self.max_inst_data < 0:
            raise ValueError(f"max_inst_data must be non-negative, got {self.max_inst_data}")
        if self.max_delay_cycles <= 0:
            raise ValueError(
                f"max_delay_cycles must be positive, got {self.max_delay_cycles}"
            )
        if self.max_unrolled_instructions <= 0:
            raise ValueError(
                "max_unrolled_instructions must be positive, "
                f"got {self.max_unrolled_instructions}"
            )
        if len(self.control_bits) != 3:
            raise ValueError(f"control_bits must contain exactly 3 indices, got {self.control_bits}")
        if len(set(self.control_bits)) != 3:
            raise ValueError(f"control_bits must be unique, got {self.control_bits}")
        invalid_control_bits = [
            bit_idx for bit_idx in self.control_bits if bit_idx < 0 or bit_idx >= self.flag_bits
        ]
        if invalid_control_bits:
            raise ValueError(
                f"control_bits out of range for {self.flag_bits} flags: {invalid_control_bits}"
            )
        invalid_disable_modes = [
            mode
            for mode in self.short_pulse_disable_modes
            if mode < 0 or mode > self.max_short_cycles + 2
        ]
        if invalid_disable_modes:
            raise ValueError(f"Invalid short-pulse disable modes: {invalid_disable_modes}")
        if self.generated_disable_mode not in self.short_pulse_disable_modes:
            raise ValueError(
                "generated_disable_mode must be one of short_pulse_disable_modes, "
                f"got {self.generated_disable_mode}"
            )

    @property
    def min_instruction_len_ns(self) -> int:
        """Minimum instruction duration in nanoseconds."""
        value = self.min_instruction_cycles * 1_000 / self.clock_mhz
        rounded = round(value)
        if abs(value - rounded) > 1e-9:
            raise ValueError(
                f"{self.name} minimum instruction length is not an integer ns value: {value}"
            )
        return int(rounded)


ESR_PRO_250 = BoardProfile()
DEFAULT_BOARD_PROFILE = ESR_PRO_250


def _duration_ns_to_cycles(duration_ns: int, clock_mhz: float) -> float:
    """Convert duration in ns to clock cycles for a given core clock."""
    return duration_ns * clock_mhz / 1_000.0


def decode_control_mode(flags: Sequence[int], profile: BoardProfile = ESR_PRO_250) -> int:
    """Decode short-pulse control mode from three control-bit indices."""
    b0, b1, b2 = profile.control_bits
    return flags[b0] + (flags[b1] << 1) + (flags[b2] << 2)


def set_control_mode(flags: list[int], mode: int, profile: BoardProfile = ESR_PRO_250) -> None:
    """Encode short-pulse control mode into a mutable flag list."""
    b0, b1, b2 = profile.control_bits
    flags[b0] = mode & 1
    flags[b1] = (mode >> 1) & 1
    flags[b2] = (mode >> 2) & 1


def validate_sequence(
    instructions: Sequence[Instruction],
    profile: BoardProfile = ESR_PRO_250,
) -> None:
    """
    Validate an instruction sequence against the configured profile.

    Raises:
        ValueError: if the sequence violates any validation rule.
    """
    if not instructions:
        raise ValueError("Instruction sequence cannot be empty")

    nr_instructions = len(instructions)
    loop_stack: list[int] = []

    if profile.wait_not_first and instructions[0].opcode == Opcode.WAIT:
        raise ValueError("WAIT is not allowed as the first instruction")

    short_pulse_modes = set(range(1, profile.max_short_cycles + 1))

    for idx, instruction in enumerate(instructions):
        if len(instruction.flags) != profile.flag_bits:
            raise ValueError(
                f"Instruction {idx} has {len(instruction.flags)} flags, "
                f"expected {profile.flag_bits}"
            )
        invalid_flag_values = [bit for bit in instruction.flags if bit not in (0, 1)]
        if invalid_flag_values:
            raise ValueError(
                f"Instruction {idx} contains non-binary flag values: {invalid_flag_values}"
            )
        if instruction.duration <= 0:
            raise ValueError(
                f"Instruction {idx} has non-positive duration: {instruction.duration}"
            )
        if not isinstance(instruction.inst_data, int):
            raise ValueError(
                f"Instruction {idx} inst_data must be int, got {type(instruction.inst_data)}"
            )
        if instruction.inst_data < 0:
            raise ValueError(f"Instruction {idx} has negative inst_data {instruction.inst_data}")
        if instruction.inst_data > profile.max_inst_data:
            raise ValueError(
                f"Instruction {idx} inst_data {instruction.inst_data} exceeds "
                f"max_inst_data {profile.max_inst_data}"
            )

        try:
            opcode = instruction.opcode
            if not isinstance(opcode, Opcode):
                opcode = Opcode(int(opcode))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Instruction {idx} has invalid opcode {instruction.opcode}") from exc

        cycles_float = _duration_ns_to_cycles(instruction.duration, profile.clock_mhz)
        cycles_rounded = round(cycles_float)
        if abs(cycles_float - cycles_rounded) > 1e-9:
            raise ValueError(
                f"Instruction {idx} duration {instruction.duration} ns does not align to "
                f"clock {profile.clock_mhz} MHz ({cycles_float:.6f} cycles)"
            )
        cycles = int(cycles_rounded)

        if cycles > profile.max_delay_cycles and opcode != Opcode.LONG_DELAY:
            raise ValueError(
                f"Instruction {idx} delay count {cycles} exceeds "
                f"max_delay_cycles {profile.max_delay_cycles} "
                f"without LONG_DELAY opcode"
            )
        if cycles < profile.min_instruction_cycles:
            raise ValueError(
                f"Instruction {idx} has {cycles} cycles, below minimum "
                f"{profile.min_instruction_cycles}"
            )

        if opcode in {Opcode.BRANCH, Opcode.JSR, Opcode.END_LOOP}:
            if instruction.inst_data >= nr_instructions:
                raise ValueError(
                    f"Instruction {idx} target {instruction.inst_data} is outside "
                    f"instruction range 0..{nr_instructions - 1}"
                )
        if opcode == Opcode.LOOP and instruction.inst_data <= 0:
            raise ValueError(
                f"Instruction {idx} LOOP iterations must be positive, "
                f"got {instruction.inst_data}"
            )
        if opcode == Opcode.LONG_DELAY and instruction.inst_data < 2:
            raise ValueError(
                f"Instruction {idx} LONG_DELAY multiplier must be at least 2, "
                f"got {instruction.inst_data}"
            )

        if opcode == Opcode.LOOP:
            loop_stack.append(idx)
        elif opcode == Opcode.END_LOOP:
            if not loop_stack:
                raise ValueError(f"Instruction {idx} END_LOOP has no matching LOOP")
            expected_start = loop_stack.pop()
            if instruction.inst_data != expected_start:
                raise ValueError(
                    f"Instruction {idx} END_LOOP points to {instruction.inst_data}, "
                    f"expected {expected_start}"
                )

        control_mode = decode_control_mode(instruction.flags, profile)
        if (
            control_mode not in short_pulse_modes
            and control_mode not in profile.short_pulse_disable_modes
        ):
            raise ValueError(
                f"Instruction {idx} uses invalid control mode {control_mode}. "
                f"Valid modes: {sorted(short_pulse_modes | profile.short_pulse_disable_modes)}"
            )

        control_bit_set = set(profile.control_bits)
        output_active = any(
            bit for bit_idx, bit in enumerate(instruction.flags) if bit_idx < profile.output_bits
        )
        non_output_flags = [
            bit_idx
            for bit_idx, bit in enumerate(instruction.flags)
            if bit_idx >= profile.output_bits and bit_idx not in control_bit_set and bit
        ]
        if non_output_flags:
            raise ValueError(
                f"Instruction {idx} sets non-output flag bits: {non_output_flags}"
            )
        if output_active and control_mode in short_pulse_modes:
            if cycles < control_mode:
                raise ValueError(
                    f"Instruction {idx} uses short-pulse mode {control_mode} cycles, "
                    f"but duration is only {cycles} cycles"
                )
            if cycles != control_mode:
                raise ValueError(
                    f"Instruction {idx} has duration {cycles} cycles but control mode "
                    f"{control_mode}; expected mode {cycles}"
                )
        if output_active and control_mode not in short_pulse_modes | profile.short_pulse_disable_modes:
            raise ValueError(
                f"Instruction {idx} active output uses unsupported control mode {control_mode}"
            )

    if loop_stack:
        raise ValueError(
            f"Missing END_LOOP for LOOP at instruction index {loop_stack[-1]}"
        )
