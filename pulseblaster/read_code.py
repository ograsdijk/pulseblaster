"""
Parser for PulseBlaster assembly code.

This module provides functions to parse PulseBlaster assembly code
and convert it to instruction sequences.
"""

import re

from .data_structures import Instruction, InstructionSequence, Opcode


def code_to_instructions(code: str) -> InstructionSequence:
    """
    Convert PulseBlaster assembly code to an InstructionSequence.

    Args:
        code (str): PulseBlaster assembly code as a string

    Returns:
        InstructionSequence: parsed instruction sequence
    """
    time_units = {"ns": 1, "us": 1e3, "ms": 1e6, "s": 1e9}
    sequence = [seq.replace("\t", "").strip() for seq in code.rstrip().split("\n")]
    sequence = [seq.split("//")[0] if "//" in seq else seq for seq in sequence]
    sequence_split = [seq.strip().split(",") for seq in sequence]
    sequence_split = [seq for seq in sequence_split if len(seq) != 1]

    addresses: dict[str, int] = {}
    sequence_processed: list[tuple[str, list[int], int, Opcode, str]] = []

    for idx, seq_str in enumerate(sequence_split):
        # get addresses
        if ":" in seq_str[0]:
            label = seq_str[0].split(":")[0].strip()
            addresses[label] = idx
            seq_str[0] = seq_str[0].split(":")[-1].strip()
        else:
            label = ""

        # convert flags
        if "0b" in seq_str[0]:
            tmp = int(seq_str[0], 2)
        elif "0x" in seq_str[0]:
            tmp = int(seq_str[0], 16)
        else:
            raise ValueError("flags not in bits or hex")
        flags = [tmp >> i & 1 for i in range(24)]

        # get the duration of each instruction
        t = re.split("([a-zA-Z].*)", seq_str[1].strip())
        t = list(filter(None, t))
        duration = int(float(t[0]) * time_units[t[1]])

        opcode = (
            Opcode[seq_str[2].strip().upper()] if len(seq_str) > 2 else Opcode.CONTINUE
        )
        inst_data_str = seq_str[3].strip() if len(seq_str) > 3 else "0"

        sequence_processed.append((label, flags, duration, opcode, inst_data_str))

    nested: list[int] = []
    for idx, seq in enumerate(sequence_processed):
        seq_list = list(seq)
        if seq[-2] == Opcode.LOOP:
            nested.append(idx)
        elif seq[-2] == Opcode.END_LOOP:
            seq_list[-1] = nested.pop()
        sequence_processed[idx] = tuple(seq_list)  # type: ignore

    # convert to a sequence of Instructions
    sequence_instructions = []
    for seq in sequence_processed:
        # set inst_data to integer addresses
        inst_data = int(addresses[seq[-1]] if seq[-1] in addresses.keys() else seq[-1])
        sequence_instructions.append(
            Instruction(
                *seq[:-1],
                inst_data=inst_data,
            )
        )

    return InstructionSequence(sequence_instructions)
