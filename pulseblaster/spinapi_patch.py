"""Write a USB-only copy of the SpinAPI 20171214 DLL; the source is never modified."""

from __future__ import annotations

import argparse
import hashlib
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
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(patched)

    return output


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
