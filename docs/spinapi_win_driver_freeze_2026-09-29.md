# SpinAPI Windows startup freeze on the CeNTREX USB PulseBlaster

**Date diagnosed:** 2026-09-29  
**Affected setup:** CeNTREX acquisition computer, SpinCore PulseBlasterESR-PRO USB, SpinAPI 20171214  
**Observed symptom:** the first SpinAPI hardware-discovery call froze/slowed the acquisition PC for about 12 seconds.

## Summary

The delay was not caused by the Python `pulseblaster` package, pulse-sequence generation, the PyPI `spinapi` wrapper, USB enumeration, or the HighFinesse wavemeter.

SpinAPI 20171214 calls its legacy PCI/WinDriver board scan before enumerating USB devices. On this acquisition computer that unused PCI scan takes about 12 seconds and makes the whole PC appear hung. The installed PulseBlaster is a USB device, so the PCI scan is unnecessary.

A USB-only copy of `spinapi64.dll` was produced by replacing the `os_count_boards(0x10E8)` call at the beginning of `pb_count_boards()` with `eax = 0`. The rest of `pb_count_boards()`, including SpinAPI's original USB enumeration, is unchanged. After Windows was confirmed to be loading the patched DLL, the approximately 12 second delay disappeared.

## Evidence

### Direct SpinAPI timing

The first diagnostic isolated the delay to native SpinAPI rather than the Python wrapper:

```text
pb_get_version()       0.000 s
pb_select_board(0)    11.914 s
pb_init()              0.046 s
pb_get_firmware_id()   0.000 s
pb_read_status()       0.008 s
pb_close()             0.001 s
```

Fresh-process tests then showed that whichever hardware-discovery function ran first paid the delay. `pb_init()` and `pb_count_boards()` could both trigger the same approximately 12 second pause. SpinCore's own software showed the same behavior, ruling out the CeNTREX Python stack.

### SpinAPI debug log

The native log showed the delay before USB enumeration:

```text
18:43:17 os_count_boards           Registering WinDriver...
18:43:17 RegisterWinDriver         Using WinDriver version 1031.
18:43:17 RegisterWinDriver         license setup succeeded

         ~12 second gap

18:43:29 os_usb_count_devices      Enumerating USB Devices...
18:43:29 os_usb_count_devices      SP9 Board Detected (pid_c1ab).
18:43:29 os_usb_count_devices      Cypress USB Chip detected. VID/PID unknown.
18:43:29 os_usb_count_devices      Enumeration Completed. Found 2 Devices.
```

The unknown Cypress device was the connected Angstrom/HighFinesse measurement device. Both it and the PulseBlaster expose the historical generic Cypress device-interface GUID. That initially made the shared USB class suspicious.

A direct Windows `SetupDiGetClassDevsW` timing test disproved that hypothesis:

```text
normal Windows USB interface:       0.001 s
Cypress interface used by SpinAPI:  0.000 s
```

Therefore the 12 second delay was not Windows enumerating the shared Cypress interface class.

### WinDriver path

The acquisition computer has the legacy Jungo driver loaded:

```text
Name        : WinDriver6
DisplayName : WinDriver6
State       : Running
StartMode   : Manual
PathName    : C:\\WINDOWS\\system32\\drivers\\windrvr6.sys
```

Inspection of SpinAPI's source and the uploaded `spinapi64.dll`, `spinapi64.lib`, and `libdriver-windows.lib` established the relevant `pb_count_boards()` flow:

```text
pb_count_boards()
    |
    +-- os_count_boards(0x10E8)   legacy PCI / WinDriver path
    |
    +-- os_usb_count_devices(0)   USB enumeration
```

The legacy PCI call was present in the exact production DLL and was the only portion removed by the patch.

## Validated binary patch

Original SpinAPI DLL:

```text
SpinAPI version: 20171214
SHA-256:
4853c8e15df34d6f5c3e421a7ee3cd7dddc708410cdb2e2351d900edb61f8c9c
```

Validated USB-only DLL:

```text
SHA-256:
13bdae314521e8edcc84a9e97216991d9c069906726a75ba0532059590655ff6
```

Patch location:

```text
Export:      pb_count_boards
RVA:         0x0000A574
File offset: 0x00009974
```

Original bytes:

```text
B9 E8 10 00 00 E8 A2 48 00 00
```

Equivalent instructions:

```asm
mov ecx, 0x10e8
call os_count_boards
```

Patched bytes:

```text
33 C0 90 90 90 90 90 90 90 90
```

Equivalent instructions:

```asm
xor eax, eax
nop
nop
nop
nop
nop
nop
nop
nop
```

The next original instruction is left intact, so SpinAPI stores zero in `num_pci_boards` and immediately continues into its unmodified USB enumeration logic.

The original DLL had no Authenticode security directory and its PE checksum field was zero, so no signature or checksum metadata needed to be repaired.

## Important deployment discovery

The first attempted replacement appeared not to help because the Python process was not loading the DLL from the SpinAPI installation's `lib` directory. Windows was actually loading:

```text
C:\\WINDOWS\\SYSTEM32\\spinapi64.dll
```

The loaded module was checked programmatically and initially had the original hash. After the actual loaded DLL was replaced, the process reported:

```text
SpinAPI version: b'20171214'
Loaded DLL: C:\\WINDOWS\\SYSTEM32\\spinapi64.dll
SHA256: 13bdae314521e8edcc84a9e97216991d9c069906726a75ba0532059590655ff6
```

Only then was the USB-only patch genuinely being tested. With that DLL loaded, the approximately 12 second delay disappeared.

## Repository-managed fix

The preferred setup is not to modify `C:\\Windows\\System32` globally.

This repository supports a local runtime override. Generate the validated DLL from either the exact original SpinAPI 20171214 DLL or an already validated patched copy:

```powershell
python -m pulseblaster.spinapi_patch "C:\\path\\to\\spinapi64.dll"
```

During migration on the acquisition PC, the currently patched `C:\\Windows\\System32\\spinapi64.dll` can be used as the source. The patcher recognizes its validated SHA-256 and copies it without changing it again.

The patcher:

1. requires the exact validated original SHA-256;
2. verifies the expected bytes at file offset `0x9974`;
3. applies only the ten-byte PCI-scan bypass;
4. verifies the complete patched SHA-256; and
5. writes `pulseblaster/_vendor/spinapi64_usb_only.dll`.

The DLL is deliberately not committed to this public repository. The runtime loader selects DLLs in this order:

1. `PULSEBLASTER_SPINAPI_DLL`, if set;
2. `pulseblaster/_vendor/spinapi64_usb_only.dll`, if present;
3. otherwise the normal upstream `spinapi` DLL lookup.

This lets the experiment use an explicit repo-controlled DLL without modifying the global SpinAPI installation.

## Verification

After generating the local DLL, start a fresh Python process:

```python
import time
import pulseblaster
import spinapi

print(spinapi.pb_get_version())

t0 = time.perf_counter()
print("boards:", spinapi.pb_count_boards())
print("elapsed:", time.perf_counter() - t0)
```

The first board count should no longer exhibit the approximately 12 second system freeze.

For an explicit path instead of the repo-local DLL:

```powershell
$env:PULSEBLASTER_SPINAPI_DLL = "C:\\path\\to\\spinapi64_usb_only.dll"
```

Set that environment variable before importing `pulseblaster`.

## Limitations

This patched DLL intentionally reports **zero SpinCore PCI/PCIe boards**. It is appropriate only on systems where the required SpinCore hardware is USB.

Do not use it on a system that needs a SpinCore PCI or PCIe board.

The HighFinesse device remains connected and usable; no change to its driver or USB configuration is part of this fix.

## Rollback

To stop using the repository override:

- remove `pulseblaster/_vendor/spinapi64_usb_only.dll`; and
- unset `PULSEBLASTER_SPINAPI_DLL`, if configured.

The upstream Python `spinapi` package will then return to its normal Windows DLL lookup.

If the global System32 DLL was changed during diagnosis, restore the backed-up original SpinAPI DLL after confirming the repo-local override works.
