"""Control how the Python spinapi wrapper resolves the native Windows DLL."""

from __future__ import annotations

import ctypes
import importlib
import os
import sys
from pathlib import Path

SPINAPI_DLL_ENV = "PULSEBLASTER_SPINAPI_DLL"
USB_ONLY_DLL_NAME = "spinapi64_usb_only.dll"
VENDORED_USB_ONLY_DLL = Path(__file__).with_name("_vendor") / USB_ONLY_DLL_NAME

_configured_dll: Path | None = None


def configure_spinapi_runtime() -> Path | None:
    """Load an explicit SpinAPI DLL before the wrapper performs its normal lookup.

    On Windows the PyPI ``spinapi`` package calls
    ``ctypes.cdll.LoadLibrary("spinapi64.dll")`` and therefore relies on the
    process-wide Windows DLL search path. CeNTREX uses a USB-only patched DLL
    that skips SpinAPI's legacy PCI/WinDriver scan.

    Resolution order:
      1. ``PULSEBLASTER_SPINAPI_DLL`` when explicitly set.
      2. ``pulseblaster/_vendor/spinapi64_usb_only.dll`` when present.
      3. No override; let the upstream spinapi package use its normal behavior.

    Returns the absolute path to the explicitly loaded DLL, or ``None`` when no
    override is active.
    """
    global _configured_dll

    if sys.platform != "win32":
        return None

    configured = os.environ.get(SPINAPI_DLL_ENV)
    if configured:
        dll_path = Path(configured).expanduser().resolve()
        if not dll_path.is_file():
            raise FileNotFoundError(
                f"{SPINAPI_DLL_ENV} points to a missing SpinAPI DLL: {dll_path}"
            )
    elif VENDORED_USB_ONLY_DLL.is_file():
        dll_path = VENDORED_USB_ONLY_DLL.resolve()
    else:
        return None

    spinapi_impl = importlib.import_module("spinapi.spinapi")
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


def configured_spinapi_dll() -> Path | None:
    """Return the DLL explicitly selected by this package, if any."""
    return _configured_dll
