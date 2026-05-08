"""
Parser for PulseBlaster assembly code.

This module provides functions to parse PulseBlaster assembly code
and convert it to instruction sequences.
"""

import re
from dataclasses import dataclass

from .data_structures import Instruction, InstructionSequence, Opcode
from .validation import ESR_PRO_250, BoardProfile, validate_sequence


@dataclass
class _ParsedInstruction:
    label: str
    flags: list[int]
    duration: int
    opcode: Opcode
    inst_data_str: str
    line_number: int


def _parse_duration(duration_token: str, line_number: int) -> int:
    """Parse an instruction duration token (e.g. ``100ns`` or ``1.5 ms``)."""
    time_units = {"ns": 1, "us": 1e3, "ms": 1e6, "s": 1e9}
    match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*([a-zA-Z]+)\s*", duration_token)
    if match is None:
        raise ValueError(
            f"Invalid duration '{duration_token}' at line {line_number}. "
            "Expected '<value><unit>', e.g. '100ns'."
        )

    value = float(match.group(1))
    unit = match.group(2).lower()
    if unit not in time_units:
        raise ValueError(
            f"Unsupported time unit '{unit}' at line {line_number}. "
            f"Supported units: {sorted(time_units)}"
        )

    return int(value * time_units[unit])


def code_to_instructions(
    code: str,
    profile: BoardProfile = ESR_PRO_250,
) -> InstructionSequence:
    """
    Convert PulseBlaster assembly code to an InstructionSequence.

    Args:
        code (str): PulseBlaster assembly code as a string
        profile (BoardProfile): board profile used to parse and validate flags

    Returns:
        InstructionSequence: parsed instruction sequence
    """
    addresses: dict[str, int] = {}
    sequence_processed: list[_ParsedInstruction] = []

    for line_number, raw_line in enumerate(code.rstrip().splitlines(), start=1):
        stripped = raw_line.split("//", maxsplit=1)[0].replace("\t", "").strip()
        if not stripped:
            continue

        seq_str = [part.strip() for part in stripped.split(",")]
        if len(seq_str) < 2:
            raise ValueError(
                f"Invalid instruction at line {line_number}: '{raw_line.strip()}'"
            )
        if len(seq_str) > 4:
            raise ValueError(
                f"Too many instruction fields at line {line_number}: '{raw_line.strip()}'"
            )

        # get addresses
        first_token = seq_str[0]
        if ":" in first_token:
            label, first_token = first_token.split(":", maxsplit=1)
            label = label.strip()
            if not label:
                raise ValueError(f"Empty label at line {line_number}")
            if label in addresses:
                raise ValueError(f"Duplicate label '{label}' at line {line_number}")
            addresses[label] = len(sequence_processed)
            first_token = first_token.strip()
        else:
            label = ""

        # convert flags
        if first_token.lower().startswith("0b"):
            tmp = int(first_token, 2)
        elif first_token.lower().startswith("0x"):
            tmp = int(first_token, 16)
        else:
            raise ValueError(f"flags not in bits or hex (line {line_number})")

        max_value = 1 << profile.flag_bits
        if not 0 <= tmp < max_value:
            raise ValueError(
                f"Flags value '{first_token}' exceeds {profile.flag_bits} bits at line {line_number}"
            )
        flags = [tmp >> i & 1 for i in range(profile.flag_bits)]

        # get the duration of each instruction
        duration = _parse_duration(seq_str[1], line_number)

        if len(seq_str) > 2:
            opcode_name = seq_str[2].strip().upper()
            try:
                opcode = Opcode[opcode_name]
            except KeyError as exc:
                raise ValueError(
                    f"Invalid opcode '{seq_str[2].strip()}' at line {line_number}"
                ) from exc
        else:
            opcode = Opcode.CONTINUE

        inst_data_str = seq_str[3].strip() if len(seq_str) > 3 else "0"

        sequence_processed.append(
            _ParsedInstruction(
                label=label,
                flags=flags,
                duration=duration,
                opcode=opcode,
                inst_data_str=inst_data_str,
                line_number=line_number,
            )
        )

    nested: list[int] = []
    for idx, seq in enumerate(sequence_processed):
        if seq.opcode == Opcode.LOOP:
            nested.append(idx)
        elif seq.opcode == Opcode.END_LOOP:
            if not nested:
                raise ValueError(
                    f"END_LOOP without matching LOOP at line {seq.line_number}"
                )
            seq.inst_data_str = str(nested.pop())
    if nested:
        start_idx = nested[-1]
        raise ValueError(
            f"LOOP without matching END_LOOP at line "
            f"{sequence_processed[start_idx].line_number}"
        )

    # convert to a sequence of Instructions
    sequence_instructions: list[Instruction] = []
    for seq in sequence_processed:
        # set inst_data to integer addresses
        inst_data_str = seq.inst_data_str
        if inst_data_str in addresses:
            inst_data = addresses[inst_data_str]
        else:
            try:
                inst_data = int(inst_data_str)
            except ValueError as exc:
                raise ValueError(
                    f"Invalid inst_data '{inst_data_str}' at line {seq.line_number}"
                ) from exc

        sequence_instructions.append(
            Instruction(
                label=seq.label,
                flags=seq.flags,
                duration=seq.duration,
                opcode=seq.opcode,
                inst_data=inst_data,
            )
        )

    validate_sequence(sequence_instructions, profile=profile)
    return InstructionSequence(sequence_instructions)
