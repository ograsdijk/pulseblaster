"""On-disk cache of compiled programs and the worker that fills it.

Compiling a long superperiod takes seconds of CPU, so callers compile in a
separate, low-priority process and keep the result on disk. A restart then
reuses earlier compiles. Each entry is one JSON file named by
:func:`pulseblaster.profiles.program_key`.

:func:`compile_to_cache` is a top-level function so a
:class:`concurrent.futures.ProcessPoolExecutor` can import it in a spawned
worker; :func:`lower_process_priority` is meant as that pool's initializer.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from .data_structures import Instruction, Opcode, unroll_duration_flags
from .generate_pulses import generate_repeating_pulses
from .profiles import package_version, signals_from_records
from .validation import BoardProfile, get_board_profile

CACHE_FORMAT_VERSION = 1


def lower_process_priority() -> None:
    """Run the current process below normal priority (best effort)."""
    try:
        if sys.platform == "win32":
            import ctypes

            below_normal_priority_class = 0x00004000
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), below_normal_priority_class)
        else:
            os.nice(10)
    except Exception:  # pragma: no cover - priority is an optimisation only
        pass


def _flags_to_int(flags: Sequence[int]) -> int:
    value = 0
    for idx, flag in enumerate(flags):
        if flag:
            value |= 1 << idx
    return value


def instructions_to_rows(instructions: Sequence[Instruction]) -> list[list[int]]:
    """Compact form: ``[flags_bitmask, opcode, inst_data, duration_ns]`` per instruction."""
    return [
        [_flags_to_int(i.flags), int(i.opcode), int(i.inst_data), int(i.duration)]
        for i in instructions
    ]


def rows_to_instructions(rows: Sequence[Sequence[int]], board: BoardProfile) -> list[Instruction]:
    out = []
    for flags, opcode, inst_data, duration in rows:
        out.append(
            Instruction(
                label="",
                flags=[(int(flags) >> bit) & 1 for bit in range(board.flag_bits)],
                duration=int(duration),
                opcode=Opcode(int(opcode)),
                inst_data=int(inst_data),
            )
        )
    return out


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def compile_to_cache(
    cache_dir: str,
    key: str,
    inputs: Mapping[str, Any],
    board_profile: str,
    meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile ``inputs`` (from :func:`profiles.compiler_inputs`) and store the result.

    Returns a short summary. Raises on compile errors so the caller's future
    carries the message.
    """
    board = get_board_profile(board_profile)
    signals = signals_from_records(inputs["signals"])
    gates = signals_from_records(inputs["gates"])
    started = time.perf_counter()
    sequence = generate_repeating_pulses(
        signals,
        masking_signals=gates or None,
        profile=board,
        progress=False,
        max_superperiod_ns=int(inputs["max_superperiod_ns"]),
        optimization=inputs["optimization"],
    )
    report: dict[str, Any] = {}
    if sequence.compilation_report is not None:
        report = asdict(sequence.compilation_report)
        report["optimization_level"] = str(getattr(report["optimization_level"], "value", report["optimization_level"]))
    report.setdefault("compile_seconds", time.perf_counter() - started)
    entry = {
        "format": CACHE_FORMAT_VERSION,
        "key": key,
        "pulseblaster_version": package_version(),
        "board_profile": board_profile,
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "inputs": dict(inputs),
        "meta": dict(meta or {}),
        "report": report,
        "instructions": instructions_to_rows(sequence.instructions),
    }
    _atomic_write_json(Path(cache_dir) / f"{key}.json", entry)
    return {"key": key, "stored_instructions": len(sequence.instructions), "report": report}


class ProgramCache:
    """Directory of compiled programs, one ``<key>.json`` file each."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)

    def path(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def has(self, key: str) -> bool:
        return self.path(key).is_file()

    def load(self, key: str) -> dict[str, Any] | None:
        try:
            entry = json.loads(self.path(key).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if entry.get("format") != CACHE_FORMAT_VERSION or entry.get("key") != key:
            return None
        return entry

    def _entries(self) -> list[tuple[Path, dict[str, Any] | None]]:
        if not self.directory.is_dir():
            return []
        out = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                with path.open(encoding="utf-8") as fh:
                    head = json.load(fh)
            except (OSError, ValueError):
                head = None
            out.append((path, head))
        return out

    def info(self) -> dict[str, Any]:
        """Totals for display; entries from other package versions are counted separately."""
        version = package_version()
        count = size = old_count = old_size = 0
        oldest: str | None = None
        for path, entry in self._entries():
            nbytes = path.stat().st_size
            count += 1
            size += nbytes
            if entry is None or entry.get("pulseblaster_version") != version:
                old_count += 1
                old_size += nbytes
            created = entry.get("created") if entry else None
            if isinstance(created, str) and (oldest is None or created < oldest):
                oldest = created
        return {
            "directory": str(self.directory),
            "count": count,
            "bytes": size,
            "oldest": oldest,
            "other_version_count": old_count,
            "other_version_bytes": old_size,
            "pulseblaster_version": version,
        }

    def clear(self, *, other_versions_only: bool = False) -> dict[str, int]:
        version = package_version()
        removed = freed = 0
        for path, entry in self._entries():
            if other_versions_only and entry is not None and entry.get("pulseblaster_version") == version:
                continue
            nbytes = path.stat().st_size
            try:
                path.unlink()
            except OSError:
                continue
            removed += 1
            freed += nbytes
        return {"removed": removed, "bytes": freed}


def compiled_timeline(
    rows: Sequence[Sequence[int]],
    board: BoardProfile | str,
    outputs: Sequence[int],
    t0_ns: float,
    t1_ns: float,
    *,
    max_edges: int = 20_000,
) -> dict[str, Any]:
    """Output levels of a compiled program between ``t0_ns`` and ``t1_ns``.

    The program repeats with its total duration, so the window may span several
    passes. For each requested output returns the level at ``t0_ns`` and its
    ``[t_ns, level]`` transitions, or ``{"dense": true}`` when the window holds
    more than ``max_edges`` transitions for that output.
    """
    board = get_board_profile(board)
    instructions = rows_to_instructions(rows, board)
    durations, flags, _ = unroll_duration_flags(instructions, board.max_unrolled_instructions)
    starts = np.concatenate(([0], np.cumsum(durations)[:-1]))
    total = int(np.sum(durations))
    if total <= 0 or not t1_ns > t0_ns:
        return {"total_ns": total, "outputs": {}}
    first_pass = int(np.floor(t0_ns / total))
    last_pass = int(np.floor(t1_ns / total))
    result: dict[str, Any] = {}
    for out in outputs:
        levels = flags[:, out].astype(np.int8)
        change = np.flatnonzero(np.diff(levels, prepend=levels[-1]) != 0)
        edge_times = starts[change]
        edge_levels = levels[change]
        # Index range of edges inside the window for each pass; count before
        # materialising so a fast carrier over a long window stays cheap.
        spans = []
        count = 0
        for p in range(first_pass, last_pass + 1):
            offset = p * total
            lo = int(np.searchsorted(edge_times, t0_ns - offset, side="right"))
            hi = int(np.searchsorted(edge_times, t1_ns - offset, side="left"))
            if hi > lo:
                spans.append((offset, lo, hi))
                count += hi - lo
            if count > max_edges:
                break
        if count > max_edges:
            result[str(out)] = {"dense": True}
            continue
        edges: list[list[float]] = []
        for offset, lo, hi in spans:
            edges.extend(
                [float(t + offset), int(v)]
                for t, v in zip(edge_times[lo:hi], edge_levels[lo:hi], strict=True)
            )
        t_in_pass = t0_ns - first_pass * total
        idx = int(np.searchsorted(starts, t_in_pass, side="right") - 1)
        result[str(out)] = {"initial": int(levels[max(idx, 0)]), "edges": edges}
    return {"total_ns": total, "outputs": result}
