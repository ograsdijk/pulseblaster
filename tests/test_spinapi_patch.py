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


def test_ensure_creates_copy_from_first_accepted_candidate(patched_source, tmp_path):
    missing = tmp_path / "missing.dll"
    unknown = tmp_path / "unknown.dll"
    unknown.write_bytes(b"something else")
    output = tmp_path / "state" / "spinapi" / "copy.dll"

    target, used = spinapi_patch.ensure_usb_only_copy(
        output, [missing, unknown, patched_source]
    )

    assert target == output.resolve()
    assert used == patched_source.resolve()
    assert output.read_bytes() == patched_source.read_bytes()
    assert list(output.parent.iterdir()) == [output.resolve()]  # no temp file left


def test_ensure_leaves_existing_copy_alone(tmp_path):
    output = tmp_path / "copy.dll"
    output.write_bytes(b"existing")
    target, used = spinapi_patch.ensure_usb_only_copy(output, [tmp_path / "nope.dll"])
    assert (target, used) == (output.resolve(), None)
    assert output.read_bytes() == b"existing"


def test_ensure_lists_every_attempt_when_nothing_is_accepted(tmp_path):
    missing = tmp_path / "missing.dll"
    unknown = tmp_path / "unknown.dll"
    unknown.write_bytes(b"something else")
    with pytest.raises(spinapi_patch.SpinapiCopyError) as info:
        spinapi_patch.ensure_usb_only_copy(tmp_path / "copy.dll", [missing, unknown])
    message = str(info.value)
    assert f"{missing}: not found" in message
    assert f"{unknown}: rejected" in message and "SHA-256" in message
    assert not (tmp_path / "copy.dll").exists()


def test_candidates_put_explicit_source_first_and_never_load(monkeypatch, tmp_path):
    monkeypatch.setenv("SystemRoot", str(tmp_path))
    monkeypatch.setattr(spinapi_patch.ctypes.util, "find_library", lambda name: "found.dll")
    explicit = tmp_path / "mine.dll"
    paths = spinapi_patch.installed_spinapi_candidates(explicit)
    assert paths[0] == explicit
    assert paths[1] == spinapi_patch.Path("found.dll")
    assert tmp_path / "System32" / "spinapi64.dll" in paths
    assert len(paths) == len(set(paths))
