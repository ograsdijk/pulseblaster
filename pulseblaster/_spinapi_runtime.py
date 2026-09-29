"""Control which native Windows DLL the Python spinapi wrapper uses.

By default nothing is overridden: the PyPI ``spinapi`` package loads
``spinapi64.dll`` through the normal Windows DLL search on its first call.

A different DLL (e.g. the USB-only copy made by ``pulseblaster.spinapi_patch``)
is used only when explicitly requested, via ``use_spinapi_dll()``, the
``spinapi_dll`` argument of ``PulseBlaster``, or ``PULSEBLASTER_SPINAPI_DLL``.
The installed SpinAPI DLL is never modified.
"""

from __future__ import annotations

import ctypes
import importlib
import os
import sys
from pathlib import Path
from typing import Any

SPINAPI_DLL_ENV = "PULSEBLASTER_SPINAPI_DLL"

_configured_dll: Path | None = None


def use_spinapi_dll(path: str | os.PathLike[str]) -> Path | None:
    """Load SpinAPI from ``path`` instead of the system ``spinapi64.dll``.

    Must be called before the first SpinAPI call in this process; the wrapper
    loads its DLL once and keeps it. Calling again with the same path is a
    no-op. Does nothing (returns ``None``) outside Windows.
    """
    global _configured_dll

    if sys.platform != "win32":
        return None

    dll_path = Path(path).expanduser().resolve()
    if _configured_dll is not None:
        if dll_path == _configured_dll:
            return dll_path
        raise RuntimeError(
            f"SpinAPI is already using {_configured_dll}; cannot switch to {dll_path}"
        )
    if not dll_path.is_file():
        raise FileNotFoundError(f"SpinAPI DLL not found: {dll_path}")

    spinapi_impl: Any = importlib.import_module("spinapi.spinapi")
    if hasattr(spinapi_impl, "_spinapi"):
        raise RuntimeError(
            "SpinAPI already loaded the system DLL; select the DLL before the first "
            "SpinAPI call"
        )
    native = ctypes.cdll.LoadLibrary(str(dll_path))

    # The public functions imported by ``spinapi`` retain the globals of the
    # implementation module, so assigning its private handle redirects all
    # subsequent wrapper calls to this explicitly loaded library.
    spinapi_impl._spinapi = native

    # Upstream normally applies this setting immediately after LoadLibrary().
    # Reproduce that behavior because setting _spinapi causes _checkloaded() to
    # skip its normal first-load block.
    spinapi_impl.pb_set_debug(spinapi_impl.debug)

    _configured_dll = dll_path
    return dll_path


def configure_spinapi_runtime() -> Path | None:
    """Apply ``PULSEBLASTER_SPINAPI_DLL`` if it is set; otherwise change nothing."""
    configured = os.environ.get(SPINAPI_DLL_ENV)
    if not configured:
        return None
    try:
        return use_spinapi_dll(configured)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"{SPINAPI_DLL_ENV}: {exc}") from exc


def configured_spinapi_dll() -> Path | None:
    """Return the DLL explicitly selected by this package, if any."""
    return _configured_dll
