# Local SpinAPI runtime

This directory is intentionally kept free of committed SpinCore binaries.

For the CeNTREX USB PulseBlaster setup, generate the validated USB-only DLL from
either the exact original SpinAPI 20171214 DLL or the already validated patched DLL:

```powershell
python -m pulseblaster.spinapi_patch "C:\\path\\to\\spinapi64.dll"
```

The command writes `spinapi64_usb_only.dll` here. On Windows, importing
`pulseblaster` will prefer this DLL over the system-wide `spinapi64.dll`.

Alternatively, set `PULSEBLASTER_SPINAPI_DLL` to an explicit DLL path.

See `docs/spinapi_win_driver_freeze_2026-09-29.md` for the diagnosis,
validated hashes, byte patch, limitations, and rollback procedure.
