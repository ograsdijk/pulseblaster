"""Tests for explicit SpinAPI native-library selection."""

from types import SimpleNamespace

import pytest

from pulseblaster import _spinapi_runtime as runtime


def test_non_windows_does_not_override(monkeypatch):
    monkeypatch.setattr(runtime.sys, "platform", "linux")
    assert runtime.configure_spinapi_runtime() is None


def test_explicit_missing_dll_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime.sys, "platform", "win32")
    missing = tmp_path / "missing.dll"
    monkeypatch.setenv(runtime.SPINAPI_DLL_ENV, str(missing))

    with pytest.raises(FileNotFoundError, match=runtime.SPINAPI_DLL_ENV):
        runtime.configure_spinapi_runtime()


def test_explicit_dll_sets_spinapi_handle(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime.sys, "platform", "win32")
    dll_path = tmp_path / "spinapi64_usb_only.dll"
    dll_path.write_bytes(b"test")
    monkeypatch.setenv(runtime.SPINAPI_DLL_ENV, str(dll_path))

    loaded = object()
    debug_calls = []
    fake_impl = SimpleNamespace(
        _spinapi=None,
        debug=False,
        pb_set_debug=debug_calls.append,
    )

    monkeypatch.setattr(runtime.importlib, "import_module", lambda _: fake_impl)
    monkeypatch.setattr(runtime.ctypes.cdll, "LoadLibrary", lambda _: loaded)

    selected = runtime.configure_spinapi_runtime()

    assert selected == dll_path.resolve()
    assert fake_impl._spinapi is loaded
    assert debug_calls == [False]
    assert runtime.configured_spinapi_dll() == dll_path.resolve()
