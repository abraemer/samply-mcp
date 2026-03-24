"""GeckoProfile data class and related types."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from samply_mcp.gecko.symbolizer import SymbolInfo


@dataclass
class FunctionInfo:
    name: str
    self_pct: float
    total_pct: float
    is_inlined: bool
    file: str | None = None
    line: int | None = None


@dataclass
class FunctionStat:
    rank: int
    name: str
    self_pct: float
    total_pct: float
    is_inlined: bool
    file: str | None = None
    line: int | None = None


@dataclass
class CallerInfo:
    name: str
    self_pct: float
    total_pct: float
    is_inlined: bool
    file: str | None = None
    line: int | None = None
    call_pct: float = 0.0


@dataclass
class CalleeInfo:
    name: str
    self_pct: float
    total_pct: float
    is_inlined: bool
    file: str | None = None
    line: int | None = None
    call_pct: float = 0.0


@dataclass
class CallerResult:
    matched_function: FunctionInfo
    match_warning: str | None
    callers: list[CallerInfo]


@dataclass
class CalleeResult:
    matched_function: FunctionInfo
    match_warning: str | None
    callees: list[CalleeInfo]


@dataclass
class CompareResult:
    duration_delta_s: float
    duration_delta_pct: float
    sample_count_a: int
    sample_count_b: int
    improved: list[dict[str, str | float]]
    regressed: list[dict[str, str | float]]
    new_hotspots: list[dict[str, str | float]]
    resolved: list[dict[str, str | float]]


class ParseError(Exception):
    pass


@dataclass
class GeckoProfile:
    sample_count: int
    self_time_by_name: dict[str, int]
    total_time_by_name: dict[str, int]
    call_edges_by_name: dict[tuple[str, str], int]
    inlined_funcs: set[str]
    duration_ms: float
    binary_path: Path | None = None
    resolved_symbols: dict[str, SymbolInfo] | None = None

    def _get_file_line(self, name: str) -> tuple[str | None, int | None]:
        if self.resolved_symbols:
            for _addr, info in self.resolved_symbols.items():
                if info.function_name == name:
                    return info.file, info.line
        return None, None

    def hot_functions(self, top_n: int) -> list[FunctionStat]:
        if self.sample_count == 0:
            return []

        sorted_funcs = sorted(
            self.self_time_by_name.items(),
            key=lambda x: x[1],
            reverse=True,
        )

        results = []
        for rank, (name, self_count) in enumerate(sorted_funcs[:top_n], 1):
            total_count = self.total_time_by_name.get(name, 0)
            is_inlined = name in self.inlined_funcs
            file, line = self._get_file_line(name)

            results.append(
                FunctionStat(
                    rank=rank,
                    name=name,
                    self_pct=round(100.0 * self_count / self.sample_count, 2),
                    total_pct=round(100.0 * total_count / self.sample_count, 2),
                    is_inlined=is_inlined,
                    file=file,
                    line=line,
                )
            )

        return results

    def _find_matching_func(self, name_fragment: str) -> tuple[str | None, str | None]:
        fragment_lower = name_fragment.lower()
        matches: dict[str, float] = {}

        all_func_names = set(self.self_time_by_name.keys()) | set(self.total_time_by_name.keys())
        for caller, callee in self.call_edges_by_name.keys():
            all_func_names.add(caller)
            all_func_names.add(callee)

        for name in all_func_names:
            if fragment_lower in name.lower():
                self_pct = (
                    100.0 * self.self_time_by_name.get(name, 0) / self.sample_count
                    if self.sample_count > 0
                    else 0.0
                )
                matches[name] = self_pct

        if not matches:
            return None, None

        sorted_matches = sorted(matches.items(), key=lambda x: x[1], reverse=True)

        name = sorted_matches[0][0]
        if len(sorted_matches) == 1:
            return name, None

        match_names = [m[0] for m in sorted_matches]
        truncated = "..." if len(match_names) > 5 else ""
        warning = (
            f"Multiple functions matched: {', '.join(match_names[:5])}"
            f"{truncated}; using highest self_pct"
        )
        return name, warning

    def callers_of(self, name_fragment: str) -> CallerResult:
        if self.sample_count == 0:
            raise ParseError("No samples in profile")

        func_name, warning = self._find_matching_func(name_fragment)
        if func_name is None:
            raise ValueError(f"No function matching '{name_fragment}' found in profile")

        self_pct = 100.0 * self.self_time_by_name.get(func_name, 0) / self.sample_count
        total_pct = 100.0 * self.total_time_by_name.get(func_name, 0) / self.sample_count
        is_inlined = func_name in self.inlined_funcs
        file, line = self._get_file_line(func_name)

        matched_info = FunctionInfo(
            name=func_name,
            self_pct=round(self_pct, 2),
            total_pct=round(total_pct, 2),
            is_inlined=is_inlined,
            file=file,
            line=line,
        )

        caller_times: dict[str, int] = {}
        for (caller_name, callee_name), count in self.call_edges_by_name.items():
            if callee_name == func_name:
                caller_times[caller_name] = caller_times.get(caller_name, 0) + count

        total_callee_samples = self.total_time_by_name.get(func_name, 0)
        callers = []
        for caller_name, count in sorted(caller_times.items(), key=lambda x: x[1], reverse=True):
            call_pct = 100.0 * count / total_callee_samples if total_callee_samples > 0 else 0.0
            caller_self_pct = (
                100.0 * self.self_time_by_name.get(caller_name, 0) / self.sample_count
                if self.sample_count > 0
                else 0.0
            )
            caller_total_pct = (
                100.0 * self.total_time_by_name.get(caller_name, 0) / self.sample_count
                if self.sample_count > 0
                else 0.0
            )
            caller_is_inlined = caller_name in self.inlined_funcs
            caller_file, caller_line = self._get_file_line(caller_name)
            callers.append(
                CallerInfo(
                    name=caller_name,
                    self_pct=round(caller_self_pct, 2),
                    total_pct=round(caller_total_pct, 2),
                    is_inlined=caller_is_inlined,
                    file=caller_file,
                    line=caller_line,
                    call_pct=round(call_pct, 2),
                )
            )

        return CallerResult(
            matched_function=matched_info,
            match_warning=warning,
            callers=callers,
        )

    def callees_of(self, name_fragment: str) -> CalleeResult:
        if self.sample_count == 0:
            raise ParseError("No samples in profile")

        func_name, warning = self._find_matching_func(name_fragment)
        if func_name is None:
            raise ValueError(f"No function matching '{name_fragment}' found in profile")

        self_pct = 100.0 * self.self_time_by_name.get(func_name, 0) / self.sample_count
        total_pct = 100.0 * self.total_time_by_name.get(func_name, 0) / self.sample_count
        is_inlined = func_name in self.inlined_funcs
        file, line = self._get_file_line(func_name)

        matched_info = FunctionInfo(
            name=func_name,
            self_pct=round(self_pct, 2),
            total_pct=round(total_pct, 2),
            is_inlined=is_inlined,
            file=file,
            line=line,
        )

        callee_times: dict[str, int] = {}
        for (caller_name, callee_name), count in self.call_edges_by_name.items():
            if caller_name == func_name:
                callee_times[callee_name] = callee_times.get(callee_name, 0) + count

        total_caller_samples = sum(callee_times.values())
        callees = []
        for callee_name, count in sorted(callee_times.items(), key=lambda x: x[1], reverse=True):
            call_pct = 100.0 * count / total_caller_samples if total_caller_samples > 0 else 0.0
            callee_self_pct = (
                100.0 * self.self_time_by_name.get(callee_name, 0) / self.sample_count
                if self.sample_count > 0
                else 0.0
            )
            callee_total_pct = (
                100.0 * self.total_time_by_name.get(callee_name, 0) / self.sample_count
                if self.sample_count > 0
                else 0.0
            )
            callee_is_inlined = callee_name in self.inlined_funcs
            callee_file, callee_line = self._get_file_line(callee_name)
            callees.append(
                CalleeInfo(
                    name=callee_name,
                    self_pct=round(callee_self_pct, 2),
                    total_pct=round(callee_total_pct, 2),
                    is_inlined=callee_is_inlined,
                    file=callee_file,
                    line=callee_line,
                    call_pct=round(call_pct, 2),
                )
            )

        return CalleeResult(
            matched_function=matched_info,
            match_warning=warning,
            callees=callees,
        )

    def compare(self, other: GeckoProfile) -> CompareResult:
        if self.sample_count == 0 or other.sample_count == 0:
            raise ParseError("Cannot compare profiles with no samples")

        duration_delta_s = other.duration_ms / 1000.0 - self.duration_ms / 1000.0
        if self.duration_ms > 0:
            duration_delta_pct = 100.0 * (other.duration_ms - self.duration_ms) / self.duration_ms
        else:
            duration_delta_pct = 0.0

        funcs_a = {
            name: 100.0 * count / self.sample_count
            for name, count in self.self_time_by_name.items()
        }
        funcs_b = {
            name: 100.0 * count / other.sample_count
            for name, count in other.self_time_by_name.items()
        }

        all_names = set(funcs_a.keys()) | set(funcs_b.keys())

        improved = []
        regressed = []
        new_hotspots = []
        resolved = []

        for name in all_names:
            pct_a = funcs_a.get(name, 0.0)
            pct_b = funcs_b.get(name, 0.0)

            if name not in funcs_a:
                new_hotspots.append({"name": name, "self_pct_b": round(pct_b, 2)})
            elif name not in funcs_b:
                resolved.append({"name": name, "self_pct_a": round(pct_a, 2)})
            else:
                delta = pct_b - pct_a
                if delta < -5.0:
                    improved.append(
                        {
                            "name": name,
                            "self_pct_a": round(pct_a, 2),
                            "self_pct_b": round(pct_b, 2),
                            "delta_pct": round(delta, 2),
                        }
                    )
                elif delta > 5.0:
                    regressed.append(
                        {
                            "name": name,
                            "self_pct_a": round(pct_a, 2),
                            "self_pct_b": round(pct_b, 2),
                            "delta_pct": round(delta, 2),
                        }
                    )

        improved.sort(key=lambda x: x["delta_pct"])
        regressed.sort(key=lambda x: x["delta_pct"], reverse=True)
        new_hotspots.sort(key=lambda x: x["self_pct_b"], reverse=True)
        resolved.sort(key=lambda x: x["self_pct_a"], reverse=True)

        return CompareResult(
            duration_delta_s=round(duration_delta_s, 3),
            duration_delta_pct=round(duration_delta_pct, 2),
            sample_count_a=self.sample_count,
            sample_count_b=other.sample_count,
            improved=improved,
            regressed=regressed,
            new_hotspots=new_hotspots,
            resolved=resolved,
        )

    @property
    def duration_s(self) -> float:
        return self.duration_ms / 1000.0
