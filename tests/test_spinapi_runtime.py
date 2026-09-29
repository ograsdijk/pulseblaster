"""Tests for explicit SpinAPI native-library selection."""

from types import SimpleNamespace

import pytest

from pulseblaster import _spinapi_runtime as runtime


@pytest.fixture(autouse=True)
def _reset_configured(monkeypatch):
    monkeypatch.setattr(runtime, "_configured_dll", None)
    monkeypatch.delenv(runtime.SPINAPI_DLL_ENV, raising=False)


@pytest.fixture
def fake_spinapi(monkeypatch):
    """Windows platform with a SpinAPI wrapper that has not loaded its DLL yet."""
    monkeypatch.setattr(runtime.sys, "platform", "win32")
    impl = SimpleNamespace(debug=False, debug_calls=[])
    impl.pb_set_debug = impl.debug_calls.append
    loaded = []
    monkeypatch.setattr(runtime.importlib, "import_module", lambda _: impl)
    monkeypatch.setattr(
        runtime.ctypes.cdll, "LoadLibrary", lambda path: loaded.append(path) or object()
    )
    impl.loaded = loaded
    return impl


def test_non_windows_does_not_override(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime.sys, "platform", "linux")
    assert runtime.use_spinapi_dll(tmp_path / "x.dll") is None


def test_no_env_leaves_installed_dll_alone(fake_spinapi):
    assert runtime.configure_spinapi_runtime() is None
    assert fake_spinapi.loaded == []
    assert not hasattr(fake_spinapi, "_spinapi")


def test_env_missing_dll_fails(fake_spinapi, monkeypatch, tmp_path):
    monkeypatch.setenv(runtime.SPINAPI_DLL_ENV, str(tmp_path / "missing.dll"))
    with pytest.raises(FileNotFoundError, match=runtime.SPINAPI_DLL_ENV):
        runtime.configure_spinapi_runtime()


def test_explicit_dll_sets_spinapi_handle(fake_spinapi, tmp_path):
    dll_path = tmp_path / "spinapi64_usb_only.dll"
    dll_path.write_bytes(b"test")

    assert runtime.use_spinapi_dll(dll_path) == dll_path.resolve()
    assert fake_spinapi._spinapi is not None
    assert fake_spinapi.debug_calls == [False]
    assert runtime.configured_spinapi_dll() == dll_path.resolve()

    # Same path again is a no-op; a different one is refused.
    assert runtime.use_spinapi_dll(dll_path) == dll_path.resolve()
    assert len(fake_spinapi.loaded) == 1
    other = tmp_path / "other.dll"
    other.write_bytes(b"test")
    with pytest.raises(RuntimeError, match="already using"):
        runtime.use_spinapi_dll(other)


def test_refuses_after_system_dll_loaded(fake_spinapi, tmp_path):
    fake_spinapi._spinapi = object()
    dll_path = tmp_path / "spinapi64_usb_only.dll"
    dll_path.write_bytes(b"test")
    with pytest.raises(RuntimeError, match="already loaded"):
        runtime.use_spinapi_dll(dll_path)
