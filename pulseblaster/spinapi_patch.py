"""Write a USB-only copy of the SpinAPI 20171214 DLL; the source is never modified."""

from __future__ import annotations

import argparse
import ctypes.util
import hashlib
import os
from collections.abc import Iterable, Sequence
from pathlib import Path

ORIGINAL_SHA256 = "4853c8e15df34d6f5c3e421a7ee3cd7dddc708410cdb2e2351d900edb61f8c9c"
PATCHED_SHA256 = "13bdae314521e8edcc84a9e97216991d9c069906726a75ba0532059590655ff6"

PATCH_OFFSET = 0x9974
ORIGINAL_BYTES = bytes.fromhex("B9 E8 10 00 00 E8 A2 48 00 00")
PATCHED_BYTES = bytes.fromhex("33 C0 90 90 90 90 90 90 90 90")

def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def patch_spinapi(source: Path, output: Path) -> Path:
    """Write a USB-only copy of a known SpinAPI 20171214 DLL to ``output``.

    The patch replaces the call to ``os_count_boards(0x10E8)`` at the start of
    ``pb_count_boards()`` with ``eax = 0``. SpinAPI's existing USB enumeration
    remains unchanged.

    Only the exact CeNTREX-tested original DLL (or an already-patched copy) is
    accepted. Unknown binaries are rejected rather than patched heuristically.
    ``source`` is only read. ``output`` must be a new file or an earlier
    USB-only copy, so an installed SpinAPI DLL is never overwritten.
    """
    source = source.expanduser().resolve()
    output = output.expanduser().resolve()

    data = source.read_bytes()
    digest = sha256(data)

    if digest == PATCHED_SHA256:
        patched = data
    elif digest == ORIGINAL_SHA256:
        actual = data[PATCH_OFFSET : PATCH_OFFSET + len(ORIGINAL_BYTES)]
        if actual != ORIGINAL_BYTES:
            raise RuntimeError(
                "Known original SHA-256 matched, but the expected patch bytes did not."
            )
        patched_data = bytearray(data)
        patched_data[PATCH_OFFSET : PATCH_OFFSET + len(PATCHED_BYTES)] = PATCHED_BYTES
        patched = bytes(patched_data)
    else:
        raise RuntimeError(
            "Refusing to patch an unknown spinapi64.dll. "
            f"SHA-256 was {digest}; expected {ORIGINAL_SHA256}."
        )

    patched_digest = sha256(patched)
    if patched_digest != PATCHED_SHA256:
        raise RuntimeError(
            "Patched DLL hash did not match the validated CeNTREX build: "
            f"{patched_digest}"
        )

    if output == source:
        raise RuntimeError("Refusing to write over the source DLL; choose another output.")
    if output.exists() and sha256(output.read_bytes()) != PATCHED_SHA256:
        raise RuntimeError(
            f"Refusing to overwrite {output}: it is not a USB-only SpinAPI copy."
        )
    if output.exists():
        return output
    output.parent.mkdir(parents=True, exist_ok=True)
    # Write next to the target and rename, so a crash never leaves a partial DLL.
    temporary = output.with_name(f"{output.name}.{os.getpid()}.tmp")
    try:
        temporary.write_bytes(patched)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)

    return output


class SpinapiCopyError(RuntimeError):
    """No installed SpinAPI DLL could be turned into the USB-only copy."""


def installed_spinapi_candidates(explicit: str | os.PathLike[str] | None = None) -> list[Path]:
    """Paths where the installed ``spinapi64.dll`` may live, in search order.

    Only paths are returned. The DLLs are never loaded, because loading one would
    bind the spinapi wrapper to it and block ``use_spinapi_dll``.
    """
    found: list[Path] = []
    if explicit:
        found.append(Path(explicit))
    try:
        located = ctypes.util.find_library("spinapi64")
    except Exception:
        located = None
    if located:
        found.append(Path(located))
    system_root = os.environ.get("SystemRoot") or os.environ.get("windir")
    if system_root:
        found.append(Path(system_root) / "System32" / "spinapi64.dll")
    for directory in ("lib", "lib64"):
        found.append(Path("C:/SpinCore/SpinAPI") / directory / "spinapi64.dll")

    unique: list[Path] = []
    for path in found:
        if path not in unique:
            unique.append(path)
    return unique


def ensure_usb_only_copy(
    output: str | os.PathLike[str],
    candidates: Iterable[str | os.PathLike[str]] | None = None,
    *,
    source: str | os.PathLike[str] | None = None,
) -> tuple[Path, Path | None]:
    """Make sure the USB-only copy ``output`` exists; return ``(output, source_used)``.

    An existing ``output`` is left alone (``source_used`` is ``None``). Otherwise the
    first candidate that ``patch_spinapi`` accepts is used; ``candidates`` defaults to
    ``installed_spinapi_candidates(source)``. The candidates are only read. If none
    is accepted a ``SpinapiCopyError`` lists every path tried and why it was rejected.
    """
    target = Path(output).expanduser().resolve()
    if target.exists():
        return target, None

    paths: Sequence[Path] = (
        [Path(c) for c in candidates]
        if candidates is not None
        else installed_spinapi_candidates(source)
    )
    attempts: list[str] = []
    for candidate in paths:
        if not candidate.is_file():
            attempts.append(f"  {candidate}: not found")
            continue
        try:
            patch_spinapi(candidate, target)
        except (RuntimeError, OSError) as exc:
            attempts.append(f"  {candidate}: rejected ({exc})")
            continue
        return target, candidate.resolve()

    listing = "\n".join(attempts) if attempts else "  (no candidate paths)"
    raise SpinapiCopyError(
        f"Cannot create the USB-only SpinAPI copy {target}; no installed SpinAPI "
        f"20171214 DLL was accepted:\n{listing}\n"
        "Pass the DLL explicitly with `python -m pulseblaster.spinapi_patch "
        "<spinapi64.dll> <output>`."
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Write a USB-only copy of the SpinAPI 20171214 DLL that skips the "
            "legacy PCI/WinDriver scan responsible for the CeNTREX startup freeze. "
            "The source DLL is not modified."
        )
    )
    parser.add_argument("source", type=Path, help="Path to the original spinapi64.dll")
    parser.add_argument("output", type=Path, help="Path of the USB-only copy to write")
    args = parser.parse_args()

    output = patch_spinapi(args.source, args.output)
    print(f"Wrote {output}")
    print(f"SHA-256: {PATCHED_SHA256}")


if __name__ == "__main__":
    main()
