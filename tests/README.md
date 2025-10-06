# PulseBlaster Tests

This directory contains comprehensive tests for the pulseblaster package using pytest.

## Test Files

- **test_data_structures.py**: Tests for data structures (Signal, Pulse, Instruction, etc.)
- **test_utils.py**: Tests for utility functions
- **test_generate_pulses.py**: Tests for pulse generation functions
- **test_read_code.py**: Tests for PulseBlaster assembly code parsing
- **conftest.py**: Pytest configuration and shared fixtures

## Running Tests

### Run all tests
```bash
pytest
```

### Run with coverage
```bash
pytest --cov=pulseblaster --cov-report=html
```

### Run specific test file
```bash
pytest tests/test_data_structures.py
```

### Run specific test class
```bash
pytest tests/test_data_structures.py::TestSignal
```

### Run specific test
```bash
pytest tests/test_data_structures.py::TestSignal::test_signal_with_duty_cycle
```

### Run with verbose output
```bash
pytest -v
```

### Run tests matching a pattern
```bash
pytest -k "validation"
```

## Test Coverage

The test suite provides comprehensive coverage of:

### Data Structures
- ✅ Signal creation and validation
- ✅ Pulse creation
- ✅ Instruction creation
- ✅ InstructionSequence generation
- ✅ Opcode enum values
- ✅ Loop handling
- ✅ Input validation for all parameters

### Utilities
- ✅ Rounding to nearest N nanoseconds
- ✅ Channel management (all_channels_off)
- ✅ Active high/low signal handling

### Pulse Generation
- ✅ Duration and cycle calculations
- ✅ Instruction length conversion
- ✅ Pulse rescaling
- ✅ Reset calculations
- ✅ Masking signal handling
- ✅ Repeating pulse generation
- ✅ Branch instruction generation
- ✅ Input validation

### Code Parsing
- ✅ Binary and hexadecimal flag parsing
- ✅ Time unit parsing (ns, us, ms, s)
- ✅ Label resolution
- ✅ Loop instruction parsing
- ✅ JSR/RTS parsing
- ✅ Branch instruction parsing
- ✅ Comment handling
- ✅ Whitespace handling

## Test Organization

Tests are organized by module and functionality:

- Each module has its own test file
- Test classes group related tests
- Fixtures provide reusable test data
- Descriptive test names explain what is being tested

## Fixtures

Common fixtures are defined in `conftest.py`:

- `simple_signal`: Basic signal for testing
- `multiple_signals`: List of varied signals
- `active_low_signal`: Signal with active_high=False
- `signal_with_offset`: Signal with time offset
- `masking_signal`: Signal for masking tests
- `sample_pulseblaster_code`: Basic assembly code
- `loop_code`: Code with loop instructions
- `jsr_code`: Code with subroutine calls

## Writing New Tests

When adding new functionality, follow these guidelines:

1. Create tests in the appropriate test file
2. Use descriptive test names: `test_<what>_<condition>`
3. Test both valid inputs and error cases
4. Use fixtures for common test data
5. Add docstrings explaining what each test validates
6. Group related tests in classes

Example:
```python
class TestNewFeature:
    """Tests for new feature."""
    
    def test_feature_with_valid_input(self):
        """Test that feature works with valid input."""
        result = new_feature(valid_input)
        assert result == expected_output
    
    def test_feature_with_invalid_input(self):
        """Test that feature raises error with invalid input."""
        with pytest.raises(ValueError):
            new_feature(invalid_input)
```

## Continuous Integration

These tests are designed to run in CI/CD pipelines. They:
- ✅ Don't require hardware (PulseBlaster board)
- ✅ Run quickly (< 1 minute for full suite)
- ✅ Provide clear error messages
- ✅ Generate coverage reports

## Test Statistics

- **Total test files**: 5
- **Test classes**: 20+
- **Individual tests**: 100+
- **Coverage target**: >90%

## Known Limitations

The test suite currently does **not** include:
- Hardware integration tests (requires PulseBlaster board)
- Performance benchmarks
- Plotting output validation (matplotlib)

These could be added as separate test modules if needed.
