"""The USB-only patcher only reads its source and never overwrites a foreign DLL."""

import pytest

from pulseblaster import spinapi_patch


@pytest.fixture
def patched_source(monkeypatch, tmp_path):
    """A source DLL whose hash counts as an already validated USB-only copy."""
    data = b"usb-only dll"
    monkeypatch.setattr(spinapi_patch, "PATCHED_SHA256", spinapi_patch.sha256(data))
    source = tmp_path / "spinapi64.dll"
    source.write_bytes(data)
    return source


def test_writes_copy_and_leaves_source(patched_source, tmp_path):
    output = tmp_path / "out" / "spinapi64_usb_only.dll"
    assert spinapi_patch.patch_spinapi(patched_source, output) == output.resolve()
    assert output.read_bytes() == patched_source.read_bytes()
    # Re-running over an earlier copy is fine.
    spinapi_patch.patch_spinapi(patched_source, output)


def test_refuses_source_as_output(patched_source):
    with pytest.raises(RuntimeError, match="source DLL"):
        spinapi_patch.patch_spinapi(patched_source, patched_source)


def test_refuses_to_overwrite_other_dll(patched_source, tmp_path):
    other = tmp_path / "spinapi64_original.dll"
    other.write_bytes(b"original")
    with pytest.raises(RuntimeError, match="not a USB-only"):
        spinapi_patch.patch_spinapi(patched_source, other)
    assert other.read_bytes() == b"original"


def test_unknown_source_is_rejected(tmp_path):
    source = tmp_path / "spinapi64.dll"
    source.write_bytes(b"something else")
    with pytest.raises(RuntimeError, match="unknown"):
        spinapi_patch.patch_spinapi(source, tmp_path / "out.dll")
