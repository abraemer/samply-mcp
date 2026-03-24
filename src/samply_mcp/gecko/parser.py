"""Gecko profile parser."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from samply_mcp.gecko.symbolizer import Symbolizer

from samply_mcp.gecko.profile import GeckoProfile, ParseError
from samply_mcp.gecko.symbolizer import SymbolInfo

logger = logging.getLogger(__name__)


@dataclass
class FrameInfo:
    func_idx: int
    inline_depth: int


@dataclass
class StackInfo:
    frame_idx: int
    prefix_idx: int | None


def parse_gecko_profile(path: Path, symbolizer: Symbolizer | None = None) -> GeckoProfile:
    raw = _load_json(path)
    return _parse(raw, path, symbolizer)


def parse_gecko_profile_from_dict(data: dict, symbolizer: Symbolizer | None = None) -> GeckoProfile:
    return _parse(data, None, symbolizer)


def _load_json(path: Path) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse JSON in {path}: {e}")
        raise ParseError(f"Invalid JSON in profile: {e}") from e
    except FileNotFoundError as e:
        logger.error(f"Profile file not found: {path}")
        raise ParseError(f"Profile file not found: {path}") from e


def _parse(raw: dict, path: Path | None, symbolizer: Symbolizer | None) -> GeckoProfile:
    sample_count = 0
    self_time_by_name: dict[str, int] = {}
    total_time_by_name: dict[str, int] = {}
    call_edges_by_name: dict[tuple[str, str], int] = {}
    inlined_funcs: set[str] = set()
    duration_ms: float = 0.0
    binary_path: Path | None = None
    resolved_symbols: dict[str, SymbolInfo] = {}

    binary_path = _extract_binary_path(raw)
    threads = raw.get("threads", [])
    if not threads:
        logger.error("No threads found in profile")
        raise ParseError("No threads in profile")

    threads_with_samples = [t for t in threads if t.get("samples", {}).get("length", 0) > 0]

    for thread in threads_with_samples:
        (
            thread_sample_count,
            thread_self_time,
            thread_total_time,
            thread_call_edges,
            thread_inlined,
            thread_duration_ms,
        ) = _parse_thread(thread, resolved_symbols)

        sample_count += thread_sample_count
        duration_ms += thread_duration_ms

        for name, count in thread_self_time.items():
            self_time_by_name[name] = self_time_by_name.get(name, 0) + count

        for name, count in thread_total_time.items():
            total_time_by_name[name] = total_time_by_name.get(name, 0) + count

        for edge, count in thread_call_edges.items():
            call_edges_by_name[edge] = call_edges_by_name.get(edge, 0) + count

        inlined_funcs.update(thread_inlined)

    if symbolizer and binary_path:
        resolved_symbols = _resolve_symbols(
            symbolizer,
            binary_path,
            self_time_by_name,
            total_time_by_name,
            call_edges_by_name,
        )
        if resolved_symbols:
            self_time_by_name = _remap_dict(self_time_by_name, resolved_symbols)
            total_time_by_name = _remap_dict(total_time_by_name, resolved_symbols)
            call_edges_by_name = _remap_call_edges(call_edges_by_name, resolved_symbols)
            inlined_funcs = {
                resolved_symbols.get(f, SymbolInfo(f, None, None)).function_name
                for f in inlined_funcs
            }

    return GeckoProfile(
        sample_count=sample_count,
        self_time_by_name=self_time_by_name,
        total_time_by_name=total_time_by_name,
        call_edges_by_name=call_edges_by_name,
        inlined_funcs=inlined_funcs,
        duration_ms=duration_ms,
        binary_path=binary_path,
        resolved_symbols=resolved_symbols if resolved_symbols else None,
    )


def _extract_binary_path(raw: dict) -> Path | None:
    libs = raw.get("libs", [])
    for lib in libs:
        if (
            lib.get("name")
            and not lib["name"].startswith("ld-linux")
            and not lib["name"].startswith("lib")
        ):
            return Path(lib["path"])
    if libs:
        for lib in libs:
            path = lib.get("path", "")
            if path and "ld-linux" not in path and not path.startswith("/usr/lib"):
                return Path(path)
    return None


def _resolve_symbols(
    symbolizer: Symbolizer,
    binary_path: Path,
    self_time_by_name: dict[str, int],
    total_time_by_name: dict[str, int],
    call_edges_by_name: dict[tuple[str, str], int],
) -> dict[str, SymbolInfo]:
    all_addresses: set[str] = set()
    for name in self_time_by_name.keys():
        if name.startswith("0x"):
            all_addresses.add(name)
    for name in total_time_by_name.keys():
        if name.startswith("0x"):
            all_addresses.add(name)
    for caller, callee in call_edges_by_name.keys():
        if caller.startswith("0x"):
            all_addresses.add(caller)
        if callee.startswith("0x"):
            all_addresses.add(callee)

    if not all_addresses:
        return {}

    try:
        return symbolizer.resolve_addresses(list(all_addresses))
    except Exception as e:
        logger.warning(f"Symbol resolution failed: {e}")
        return {}


def _remap_dict(d: dict[str, int], resolved: dict[str, SymbolInfo]) -> dict[str, int]:
    result: dict[str, int] = {}
    for name, count in d.items():
        new_name = resolved.get(name, SymbolInfo(name, None, None)).function_name
        result[new_name] = result.get(new_name, 0) + count
    return result


def _remap_call_edges(
    edges: dict[tuple[str, str], int], resolved: dict[str, SymbolInfo]
) -> dict[tuple[str, str], int]:
    result: dict[tuple[str, str], int] = {}
    for (caller, callee), count in edges.items():
        new_caller = resolved.get(caller, SymbolInfo(caller, None, None)).function_name
        new_callee = resolved.get(callee, SymbolInfo(callee, None, None)).function_name
        key = (new_caller, new_callee)
        result[key] = result.get(key, 0) + count
    return result


def _parse_thread(
    thread: dict, resolved_symbols: dict[str, SymbolInfo]
) -> tuple[
    int,
    dict[str, int],
    dict[str, int],
    dict[tuple[str, str], int],
    set[str],
    float,
]:
    strings = thread.get("stringArray", [])

    frames = _parse_frame_table(thread, strings)
    stacks = _parse_stack_table(thread)
    samples, duration_ms = _parse_samples(thread)

    sample_count = len(samples)

    thread_self_time: dict[int, int] = {}
    thread_total_time: dict[int, int] = {}
    thread_call_edges: dict[tuple[int, int], int] = {}
    thread_inlined: set[int] = set()

    for frame in frames:
        if frame.inline_depth > 0:
            thread_inlined.add(frame.func_idx)

    for sample_stack_idx in samples:
        if sample_stack_idx >= len(stacks):
            continue

        chain = _get_stack_chain(sample_stack_idx, stacks)
        seen_funcs: set[int] = set()

        for i, frame_idx in enumerate(chain):
            if frame_idx >= len(frames):
                continue
            frame = frames[frame_idx]
            func_idx = frame.func_idx

            if i == 0:
                thread_self_time[func_idx] = thread_self_time.get(func_idx, 0) + 1

            if func_idx not in seen_funcs:
                thread_total_time[func_idx] = thread_total_time.get(func_idx, 0) + 1
                seen_funcs.add(func_idx)

        for i in range(len(chain) - 1):
            caller_frame_idx = chain[i + 1]
            callee_frame_idx = chain[i]
            if caller_frame_idx < len(frames) and callee_frame_idx < len(frames):
                caller_func = frames[caller_frame_idx].func_idx
                callee_func = frames[callee_frame_idx].func_idx
                edge = (caller_func, callee_func)
                thread_call_edges[edge] = thread_call_edges.get(edge, 0) + 1

    func_names: dict[int, str] = {}
    func_table = thread.get("funcTable", {})
    func_name_indices = func_table.get("name", [])
    for func_idx, name_idx in enumerate(func_name_indices):
        if name_idx < len(strings):
            func_names[func_idx] = strings[name_idx]

    self_time_by_name: dict[str, int] = {}
    total_time_by_name: dict[str, int] = {}
    call_edges_by_name: dict[tuple[str, str], int] = {}
    inlined_funcs: set[str] = set()

    def get_resolved_name(name: str) -> str:
        if name.startswith("0x") and name in resolved_symbols:
            return resolved_symbols[name].function_name
        return name

    for func_idx, count in thread_self_time.items():
        name = func_names.get(func_idx, f"0x{func_idx:x}")
        resolved_name = get_resolved_name(name)
        self_time_by_name[resolved_name] = self_time_by_name.get(resolved_name, 0) + count

    for func_idx, count in thread_total_time.items():
        name = func_names.get(func_idx, f"0x{func_idx:x}")
        resolved_name = get_resolved_name(name)
        total_time_by_name[resolved_name] = total_time_by_name.get(resolved_name, 0) + count

    for (caller_func, callee_func), count in thread_call_edges.items():
        caller_name = func_names.get(caller_func, f"0x{caller_func:x}")
        callee_name = func_names.get(callee_func, f"0x{callee_func:x}")
        resolved_caller = get_resolved_name(caller_name)
        resolved_callee = get_resolved_name(callee_name)
        edge = (resolved_caller, resolved_callee)
        call_edges_by_name[edge] = call_edges_by_name.get(edge, 0) + count

    for func_idx in thread_inlined:
        name = func_names.get(func_idx, f"0x{func_idx:x}")
        resolved_name = get_resolved_name(name)
        inlined_funcs.add(resolved_name)

    return (
        sample_count,
        self_time_by_name,
        total_time_by_name,
        call_edges_by_name,
        inlined_funcs,
        duration_ms,
    )


def _parse_frame_table(thread: dict, strings: list[str]) -> list[FrameInfo]:
    frame_table = thread.get("frameTable", {})
    func_indices = frame_table.get("func", [])
    inline_depths = frame_table.get("inlineDepth", [])
    length = frame_table.get("length", len(func_indices))

    frames = []
    for i in range(length):
        func_idx = func_indices[i] if i < len(func_indices) else 0
        inline_depth = inline_depths[i] if i < len(inline_depths) else 0
        frames.append(FrameInfo(func_idx=func_idx, inline_depth=inline_depth))

    return frames


def _parse_stack_table(thread: dict) -> list[StackInfo]:
    stack_table = thread.get("stackTable", {})
    prefixes = stack_table.get("prefix", [])
    frames = stack_table.get("frame", [])
    length = stack_table.get("length", len(frames))

    stacks = []
    for i in range(length):
        prefix = prefixes[i] if i < len(prefixes) else None
        frame = frames[i] if i < len(frames) else 0
        stacks.append(StackInfo(frame_idx=frame, prefix_idx=prefix))

    return stacks


def _parse_samples(thread: dict) -> tuple[list[int], float]:
    samples = thread.get("samples", {})
    stack_indices = samples.get("stack", [])

    time_deltas = samples.get("timeDeltas", [])
    duration_ms = sum(time_deltas) / 1000.0 if time_deltas else 0.0

    valid_samples = [s for s in stack_indices if s is not None]
    return valid_samples, duration_ms


def _get_stack_chain(stack_idx: int, stacks: list[StackInfo]) -> list[int]:
    chain = []
    visited = set()
    current = stack_idx

    while current is not None and current not in visited:
        visited.add(current)
        if current >= len(stacks):
            break
        stack_info = stacks[current]
        chain.append(stack_info.frame_idx)
        current = stack_info.prefix_idx

    return chain
