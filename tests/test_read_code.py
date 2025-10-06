"""Tests for read_code module."""

import pytest

from pulseblaster.data_structures import InstructionSequence, Opcode
from pulseblaster.read_code import code_to_instructions


class TestCodeToInstructions:
    """Tests for code_to_instructions function."""

    def test_simple_code(self):
        """Test parsing simple PulseBlaster code."""
        code = """
        0x000001, 100ns, CONTINUE
        0x000000, 200ns, STOP
        """
        sequence = code_to_instructions(code)
        assert isinstance(sequence, InstructionSequence)
        assert len(sequence.instructions) == 2
        assert sequence.instructions[0].duration == 100
        assert sequence.instructions[1].duration == 200
        assert sequence.instructions[1].opcode == Opcode.STOP

    def test_binary_flags(self):
        """Test parsing binary flags."""
        code = "0b000000000000000000000001, 100ns, CONTINUE"
        sequence = code_to_instructions(code)
        assert sequence.instructions[0].flags[0] == 1
        assert sum(sequence.instructions[0].flags) == 1

    def test_hex_flags(self):
        """Test parsing hexadecimal flags."""
        code = "0xFFFFFF, 100ns, CONTINUE"
        sequence = code_to_instructions(code)
        assert sum(sequence.instructions[0].flags[:24]) == 24

    def test_with_comments(self):
        """Test parsing code with comments."""
        code = """
        // This is a comment
        0x000001, 100ns, CONTINUE  // inline comment
        0x000000, 200ns, STOP
        """
        sequence = code_to_instructions(code)
        assert len(sequence.instructions) == 2

    def test_with_labels(self):
        """Test parsing code with labels."""
        code = """
        start: 0x000001, 100ns, CONTINUE
        0x000000, 200ns, BRANCH, start
        """
        sequence = code_to_instructions(code)
        assert sequence.instructions[0].label == "start"
        assert sequence.instructions[1].opcode == Opcode.BRANCH
        assert sequence.instructions[1].inst_data == 0  # Should point to index 0

    def test_loop_instructions(self):
        """Test parsing LOOP and END_LOOP instructions."""
        code = """
        0x000001, 100ns, LOOP, 5
        0x000000, 200ns, END_LOOP
        0x000001, 100ns, STOP
        """
        sequence = code_to_instructions(code)
        assert sequence.instructions[0].opcode == Opcode.LOOP
        assert sequence.instructions[0].inst_data == 5
        assert sequence.instructions[1].opcode == Opcode.END_LOOP
        # END_LOOP should point back to LOOP
        assert sequence.instructions[1].inst_data == 0

    def test_jsr_rts(self):
        """Test parsing JSR and RTS instructions."""
        code = """
        0x000001, 100ns, JSR, subroutine
        0x000000, 200ns, STOP
        subroutine: 0x000001, 50ns, CONTINUE
        0x000000, 50ns, RTS
        """
        sequence = code_to_instructions(code)
        assert sequence.instructions[0].opcode == Opcode.JSR
        assert sequence.instructions[0].inst_data == 2  # Should point to subroutine
        assert sequence.instructions[3].opcode == Opcode.RTS

    def test_various_time_units(self):
        """Test parsing various time units."""
        code = """
        0x000001, 100ns, CONTINUE
        0x000001, 50us, CONTINUE
        0x000001, 10ms, CONTINUE
        0x000001, 1s, CONTINUE
        """
        sequence = code_to_instructions(code)
        assert sequence.instructions[0].duration == 100
        assert sequence.instructions[1].duration == 50 * 1000
        assert sequence.instructions[2].duration == 10 * 1000 * 1000
        assert sequence.instructions[3].duration == 1 * 1000 * 1000 * 1000

    def test_default_opcode(self):
        """Test that CONTINUE is used as default opcode."""
        code = "0x000001, 100ns"
        sequence = code_to_instructions(code)
        assert sequence.instructions[0].opcode == Opcode.CONTINUE

    def test_default_inst_data(self):
        """Test that inst_data defaults to 0."""
        code = "0x000001, 100ns, CONTINUE"
        sequence = code_to_instructions(code)
        assert sequence.instructions[0].inst_data == 0

    def test_sos_example(self):
        """Test parsing the SOS example from documentation."""
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
        sequence = code_to_instructions(code)
        assert isinstance(sequence, InstructionSequence)
        assert len(sequence.instructions) > 0
        # Check that addresses were resolved
        assert sequence.instructions[1].opcode == Opcode.JSR

    def test_loop_example(self):
        """Test parsing the loop example from documentation."""
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
        assert isinstance(sequence, InstructionSequence)
        assert len(sequence.instructions) > 0

    def test_nested_loops(self):
        """Test parsing nested loops."""
        code = """
        0x000001, 100ns, LOOP, 3
        0x000002, 50ns, LOOP, 2
        0x000003, 25ns, END_LOOP
        0x000004, 50ns, END_LOOP
        0x000005, 100ns, STOP
        """
        sequence = code_to_instructions(code)
        assert len(sequence.instructions) == 5
        assert sequence.instructions[0].opcode == Opcode.LOOP
        assert sequence.instructions[1].opcode == Opcode.LOOP
        assert sequence.instructions[2].opcode == Opcode.END_LOOP
        assert sequence.instructions[3].opcode == Opcode.END_LOOP

    def test_floating_point_duration(self):
        """Test parsing floating point duration values."""
        code = "0x000001, 123.5ns, CONTINUE"
        sequence = code_to_instructions(code)
        assert sequence.instructions[0].duration == 123  # Should be converted to int

    def test_whitespace_handling(self):
        """Test that various whitespace formats are handled."""
        code = """
        0x000001,100ns,CONTINUE
        0x000002,  200ns  ,  CONTINUE
        \t0x000003,\t300ns,\tSTOP
        """
        sequence = code_to_instructions(code)
        assert len(sequence.instructions) == 3

    def test_empty_lines_ignored(self):
        """Test that empty lines are ignored."""
        code = """
        0x000001, 100ns, CONTINUE

        0x000002, 200ns, CONTINUE

        """
        sequence = code_to_instructions(code)
        assert len(sequence.instructions) == 2

    def test_case_insensitive_opcodes(self):
        """Test that opcodes are case insensitive."""
        code = """
        0x000001, 100ns, continue
        0x000002, 200ns, Stop
        """
        sequence = code_to_instructions(code)
        assert sequence.instructions[0].opcode == Opcode.CONTINUE
        assert sequence.instructions[1].opcode == Opcode.STOP

    def test_wait_opcode(self):
        """Test WAIT opcode."""
        code = "0x000000, 100ns, WAIT"
        sequence = code_to_instructions(code)
        assert sequence.instructions[0].opcode == Opcode.WAIT

    def test_long_delay_opcode(self):
        """Test LONG_DELAY opcode."""
        code = "0x000000, 100ns, LONG_DELAY"
        sequence = code_to_instructions(code)
        assert sequence.instructions[0].opcode == Opcode.LONG_DELAY

    def test_invalid_flags_format(self):
        """Test that invalid flags format raises an error."""
        code = "12345, 100ns, CONTINUE"  # Not binary or hex
        with pytest.raises(ValueError, match="flags not in bits or hex"):
            code_to_instructions(code)

    def test_label_resolution(self):
        """Test that labels are properly resolved to addresses."""
        code = """
        loop_start: 0x000001, 100ns, CONTINUE
        0x000002, 200ns, CONTINUE
        end: 0x000003, 300ns, BRANCH, loop_start
        """
        sequence = code_to_instructions(code)
        # BRANCH should point to index 0
        assert sequence.instructions[2].inst_data == 0

    def test_multiple_labels(self):
        """Test code with multiple labels."""
        code = """
        label1: 0x000001, 100ns, CONTINUE
        label2: 0x000002, 200ns, CONTINUE
        label3: 0x000003, 300ns, BRANCH, label2
        """
        sequence = code_to_instructions(code)
        assert sequence.instructions[2].inst_data == 1  # Should point to label2

    def test_instruction_sequence_properties(self):
        """Test that InstructionSequence properties are correctly populated."""
        code = """
        0x000001, 100ns, CONTINUE
        0x000000, 200ns, BRANCH, 0
        """
        sequence = code_to_instructions(code)
        assert len(sequence.duration) > 0
        assert len(sequence.flags) > 0
        assert sequence.branch_index is not None
