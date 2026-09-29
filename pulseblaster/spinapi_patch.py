"""Create the CeNTREX USB-only SpinAPI 20171214 DLL from a SpinCore DLL."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

ORIGINAL_SHA256 = "4853c8e15df34d6f5c3e421a7ee3cd7dddc708410cdb2e2351d900edb61f8c9c"
PATCHED_SHA256 = "13bdae314521e8edcc84a9e97216991d9c069906726a75ba0532059590655ff6"

PATCH_OFFSET = 0x9974
ORIGINAL_BYTES = bytes.fromhex("B9 E8 10 00 00 E8 A2 48 00 00")
PATCHED_BYTES = bytes.fromhex("33 C0 90 90 90 90 90 90 90 90")

DEFAULT_OUTPUT = Path(__file__).with_name("_vendor") / "spinapi64_usb_only.dll"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def patch_spinapi(source: Path, output: Path = DEFAULT_OUTPUT) -> Path:
    """Patch a known SpinAPI 20171214 DLL and write a USB-only copy.

    The patch replaces the call to ``os_count_boards(0x10E8)`` at the start of
    ``pb_count_boards()`` with ``eax = 0``. SpinAPI's existing USB enumeration
    remains unchanged.

    Only the exact CeNTREX-tested original DLL (or an already-patched copy) is
    accepted. Unknown binaries are rejected rather than patched heuristically.
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

    output.parent.mkdir(parents=True, exist_ok=True)
    if source != output:
        output.write_bytes(patched)
    elif digest == PATCHED_SHA256:
        pass
    else:
        raise RuntimeError("Refusing to patch the source DLL in place.")

    return output


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Create a USB-only SpinAPI 20171214 DLL that skips the legacy "
            "PCI/WinDriver scan responsible for the CeNTREX startup freeze."
        )
    )
    parser.add_argument("source", type=Path, help="Path to the original spinapi64.dll")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output path (default: {DEFAULT_OUTPUT})",
    )
    args = parser.parse_args()

    output = patch_spinapi(args.source, args.output)
    print(f"Wrote {output}")
    print(f"SHA-256: {PATCHED_SHA256}")


if __name__ == "__main__":
    main()
