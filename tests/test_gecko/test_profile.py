"""Tests for GeckoProfile parser."""

import json
import tempfile
from pathlib import Path

import pytest

from samply_mcp.gecko import (
    CalleeResult,
    CallerResult,
    CompareResult,
    FunctionStat,
    ParseError,
    parse_gecko_profile,
)


def create_minimal_profile(
    strings: list[str],
    frames: list[dict],
    stacks: list[dict],
    samples: list[int],
    time_deltas: list[int] | None = None,
) -> dict:
    func_table_length = len(set(f.get("func", i) for i, f in enumerate(frames)))
    func_names = [frames[i].get("func_name_idx", i) for i in range(func_table_length)]

    return {
        "meta": {"interval": 1.0},
        "threads": [
            {
                "name": "main",
                "isMainThread": True,
                "stringArray": strings,
                "frameTable": {
                    "length": len(frames),
                    "func": [f.get("func", i) for i, f in enumerate(frames)],
                    "inlineDepth": [f.get("inline_depth", 0) for f in frames],
                },
                "funcTable": {
                    "length": func_table_length,
                    "name": func_names,
                },
                "stackTable": {
                    "length": len(stacks),
                    "prefix": [s.get("prefix") for s in stacks],
                    "frame": [s.get("frame") for s in stacks],
                },
                "samples": {
                    "length": len(samples),
                    "stack": samples,
                    "timeDeltas": time_deltas or [1] * len(samples),
                },
            }
        ],
    }


def test_parse_reference_profile():
    profile = parse_gecko_profile(Path("reference/profile.json"))
    assert profile.sample_count > 0
    assert profile.duration_s > 0

    hot = profile.hot_functions(20)
    assert len(hot) > 0
    assert all(isinstance(h, FunctionStat) for h in hot)
    assert all(h.self_pct >= 0 for h in hot)

    top = hot[0]
    assert top.rank == 1
    assert top.self_pct > 0
    assert top.total_pct >= top.self_pct


def test_hot_functions_returns_top_n():
    strings = ["root", "main", "foo", "bar", "baz"]
    frames = [
        {"func": 0, "func_name_idx": 0},
        {"func": 1, "func_name_idx": 1},
        {"func": 2, "func_name_idx": 2},
        {"func": 3, "func_name_idx": 3},
    ]
    stacks = [
        {"frame": 0, "prefix": None},
        {"frame": 1, "prefix": 0},
        {"frame": 2, "prefix": 1},
        {"frame": 3, "prefix": 1},
    ]
    samples = [2, 2, 2, 3, 3]

    profile_data = create_minimal_profile(strings, frames, stacks, samples)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(profile_data, f)
        f.flush()
        profile = parse_gecko_profile(Path(f.name))

    hot = profile.hot_functions(10)
    assert len(hot) <= 10
    assert all(isinstance(h, FunctionStat) for h in hot)
    assert all(h.self_pct >= 0 for h in hot)
    assert all(h.total_pct >= h.self_pct for h in hot)


def test_self_time_calculation():
    strings = ["root", "leaf"]
    frames = [{"func": 0, "func_name_idx": 0}, {"func": 1, "func_name_idx": 1}]
    stacks = [
        {"frame": 0, "prefix": None},
        {"frame": 1, "prefix": 0},
    ]
    samples = [1, 1, 1]

    profile_data = create_minimal_profile(strings, frames, stacks, samples)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(profile_data, f)
        f.flush()
        profile = parse_gecko_profile(Path(f.name))

    hot = profile.hot_functions(10)

    leaf_func = next((h for h in hot if h.name == "leaf"), None)
    assert leaf_func is not None
    assert leaf_func.self_pct == 100.0
    assert leaf_func.total_pct == 100.0

    root_func = next((h for h in hot if h.name == "root"), None)
    assert root_func is None


def test_total_time_includes_ancestors():
    strings = ["root", "middle", "leaf"]
    frames = [
        {"func": 0, "func_name_idx": 0},
        {"func": 1, "func_name_idx": 1},
        {"func": 2, "func_name_idx": 2},
    ]
    stacks = [
        {"frame": 0, "prefix": None},
        {"frame": 1, "prefix": 0},
        {"frame": 2, "prefix": 1},
    ]
    samples = [2]

    profile_data = create_minimal_profile(strings, frames, stacks, samples)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(profile_data, f)
        f.flush()
        profile = parse_gecko_profile(Path(f.name))

    hot = profile.hot_functions(10)

    for h in hot:
        assert h.total_pct == 100.0, f"{h.name} should have total_pct=100"

    leaf = next(h for h in hot if h.name == "leaf")
    assert leaf.self_pct == 100.0


def test_callers_of():
    strings = ["caller", "callee"]
    frames = [{"func": 0, "func_name_idx": 0}, {"func": 1, "func_name_idx": 1}]
    stacks = [
        {"frame": 0, "prefix": None},
        {"frame": 1, "prefix": 0},
    ]
    samples = [1, 1, 1]

    profile_data = create_minimal_profile(strings, frames, stacks, samples)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(profile_data, f)
        f.flush()
        profile = parse_gecko_profile(Path(f.name))

    result = profile.callers_of("callee")
    assert isinstance(result, CallerResult)
    assert result.matched_function.name == "callee"
    assert len(result.callers) == 1
    assert result.callers[0].name == "caller"


def test_callers_of_case_insensitive():
    strings = ["MyFunction", "OtherFunc"]
    frames = [{"func": 0, "func_name_idx": 0}, {"func": 1, "func_name_idx": 1}]
    stacks = [{"frame": 0, "prefix": None}, {"frame": 1, "prefix": 0}]
    samples = [1]

    profile_data = create_minimal_profile(strings, frames, stacks, samples)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(profile_data, f)
        f.flush()
        profile = parse_gecko_profile(Path(f.name))

    result = profile.callers_of("myfunction")
    assert result.matched_function.name == "MyFunction"


def test_callers_of_multiple_matches_warning():
    strings = ["foo_a", "foo_b", "caller"]
    frames = [
        {"func": 0, "func_name_idx": 0},
        {"func": 1, "func_name_idx": 1},
        {"func": 2, "func_name_idx": 2},
    ]
    stacks = [
        {"frame": 2, "prefix": None},
        {"frame": 0, "prefix": 2},
        {"frame": 1, "prefix": 2},
    ]
    samples = [1, 2]

    profile_data = create_minimal_profile(strings, frames, stacks, samples)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(profile_data, f)
        f.flush()
        profile = parse_gecko_profile(Path(f.name))

    result = profile.callers_of("foo")
    assert result.match_warning is not None
    assert "Multiple functions matched" in result.match_warning


def test_callers_of_not_found():
    strings = ["foo", "bar"]
    frames = [{"func": 0, "func_name_idx": 0}, {"func": 1, "func_name_idx": 1}]
    stacks = [{"frame": 0, "prefix": None}]
    samples = [0]

    profile_data = create_minimal_profile(strings, frames, stacks, samples)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(profile_data, f)
        f.flush()
        profile = parse_gecko_profile(Path(f.name))

    with pytest.raises(ValueError, match="No function matching"):
        profile.callers_of("notfound")


def test_callees_of():
    strings = ["caller", "callee1", "callee2"]
    frames = [
        {"func": 0, "func_name_idx": 0},
        {"func": 1, "func_name_idx": 1},
        {"func": 2, "func_name_idx": 2},
    ]
    stacks = [
        {"frame": 0, "prefix": None},
        {"frame": 1, "prefix": 0},
        {"frame": 2, "prefix": 0},
    ]
    samples = [1, 2]

    profile_data = create_minimal_profile(strings, frames, stacks, samples)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(profile_data, f)
        f.flush()
        profile = parse_gecko_profile(Path(f.name))

    result = profile.callees_of("caller")
    assert isinstance(result, CalleeResult)
    assert result.matched_function.name == "caller"
    assert len(result.callees) == 2


def test_compare_detects_improvements():
    strings_a = ["improved_func", "unchanged"]
    frames_a = [
        {"func": 0, "func_name_idx": 0},
        {"func": 1, "func_name_idx": 1},
    ]
    stacks_a = [{"frame": 0, "prefix": None}, {"frame": 1, "prefix": None}]
    samples_a = [0] * 80 + [1] * 20

    profile_a_data = create_minimal_profile(strings_a, frames_a, stacks_a, samples_a)

    strings_b = ["improved_func", "unchanged"]
    frames_b = [
        {"func": 0, "func_name_idx": 0},
        {"func": 1, "func_name_idx": 1},
    ]
    stacks_b = [{"frame": 0, "prefix": None}, {"frame": 1, "prefix": None}]
    samples_b = [0] * 10 + [1] * 90

    profile_b_data = create_minimal_profile(strings_b, frames_b, stacks_b, samples_b)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(profile_a_data, f)
        f.flush()
        profile_a = parse_gecko_profile(Path(f.name))

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(profile_b_data, f)
        f.flush()
        profile_b = parse_gecko_profile(Path(f.name))

    result = profile_a.compare(profile_b)
    assert isinstance(result, CompareResult)
    assert result.sample_count_a == 100
    assert result.sample_count_b == 100

    improved_names = [i["name"] for i in result.improved]
    assert "improved_func" in improved_names


def test_compare_detects_regressions():
    strings_a = ["regressed_func", "other"]
    frames_a = [{"func": 0, "func_name_idx": 0}, {"func": 1, "func_name_idx": 1}]
    stacks_a = [{"frame": 0, "prefix": None}, {"frame": 1, "prefix": None}]
    samples_a = [0] * 10 + [1] * 90

    strings_b = ["regressed_func", "other"]
    frames_b = [{"func": 0, "func_name_idx": 0}, {"func": 1, "func_name_idx": 1}]
    stacks_b = [{"frame": 0, "prefix": None}, {"frame": 1, "prefix": None}]
    samples_b = [0] * 80 + [1] * 20

    profile_a_data = create_minimal_profile(strings_a, frames_a, stacks_a, samples_a)
    profile_b_data = create_minimal_profile(strings_b, frames_b, stacks_b, samples_b)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(profile_a_data, f)
        f.flush()
        profile_a = parse_gecko_profile(Path(f.name))

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(profile_b_data, f)
        f.flush()
        profile_b = parse_gecko_profile(Path(f.name))

    result = profile_a.compare(profile_b)

    regressed_names = [r["name"] for r in result.regressed]
    assert "regressed_func" in regressed_names


def test_compare_detects_new_hotspots():
    strings_a = ["existing"]
    frames_a = [{"func": 0, "func_name_idx": 0}]
    stacks_a = [{"frame": 0, "prefix": None}]
    samples_a = [0] * 100

    strings_b = ["existing", "new_hotspot"]
    frames_b = [
        {"func": 0, "func_name_idx": 0},
        {"func": 1, "func_name_idx": 1},
    ]
    stacks_b = [{"frame": 0, "prefix": None}, {"frame": 1, "prefix": None}]
    samples_b = [0] * 50 + [1] * 50

    profile_a_data = create_minimal_profile(strings_a, frames_a, stacks_a, samples_a)
    profile_b_data = create_minimal_profile(strings_b, frames_b, stacks_b, samples_b)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(profile_a_data, f)
        f.flush()
        profile_a = parse_gecko_profile(Path(f.name))

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(profile_b_data, f)
        f.flush()
        profile_b = parse_gecko_profile(Path(f.name))

    result = profile_a.compare(profile_b)

    new_names = [n["name"] for n in result.new_hotspots]
    assert "new_hotspot" in new_names


def test_compare_detects_resolved():
    strings_a = ["resolved_func", "other"]
    frames_a = [
        {"func": 0, "func_name_idx": 0},
        {"func": 1, "func_name_idx": 1},
    ]
    stacks_a = [{"frame": 0, "prefix": None}, {"frame": 1, "prefix": None}]
    samples_a = [0] * 50 + [1] * 50

    strings_b = ["other"]
    frames_b = [{"func": 0, "func_name_idx": 0}]
    stacks_b = [{"frame": 0, "prefix": None}]
    samples_b = [0] * 100

    profile_a_data = create_minimal_profile(strings_a, frames_a, stacks_a, samples_a)
    profile_b_data = create_minimal_profile(strings_b, frames_b, stacks_b, samples_b)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(profile_a_data, f)
        f.flush()
        profile_a = parse_gecko_profile(Path(f.name))

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(profile_b_data, f)
        f.flush()
        profile_b = parse_gecko_profile(Path(f.name))

    result = profile_a.compare(profile_b)

    resolved_names = [r["name"] for r in result.resolved]
    assert "resolved_func" in resolved_names


def test_malformed_json():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write("not valid json{")
        f.flush()

        with pytest.raises(ParseError, match="Invalid JSON"):
            parse_gecko_profile(Path(f.name))


def test_missing_file():
    with pytest.raises(ParseError, match="Profile file not found"):
        parse_gecko_profile(Path("/nonexistent/path/profile.json"))


def test_empty_threads():
    profile_data = {"meta": {}, "threads": []}
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(profile_data, f)
        f.flush()

        with pytest.raises(ParseError, match="No threads"):
            parse_gecko_profile(Path(f.name))


def test_no_samples():
    profile_data = {
        "meta": {},
        "threads": [
            {
                "name": "main",
                "isMainThread": True,
                "stringArray": [],
                "frameTable": {"length": 0, "func": [], "inlineDepth": []},
                "funcTable": {"length": 0, "name": []},
                "stackTable": {"length": 0, "prefix": [], "frame": []},
                "samples": {"length": 0, "stack": [], "timeDeltas": []},
            }
        ],
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(profile_data, f)
        f.flush()
        profile = parse_gecko_profile(Path(f.name))

        assert profile.sample_count == 0
        assert profile.hot_functions(10) == []


def test_inlined_function_detection():
    strings = ["normal_func", "inlined_func"]
    frames = [
        {"func": 0, "func_name_idx": 0, "inline_depth": 0},
        {"func": 1, "func_name_idx": 1, "inline_depth": 1},
    ]
    stacks = [{"frame": 0, "prefix": None}, {"frame": 1, "prefix": 0}]
    samples = [1]

    profile_data = create_minimal_profile(strings, frames, stacks, samples)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(profile_data, f)
        f.flush()
        profile = parse_gecko_profile(Path(f.name))

    hot = profile.hot_functions(10)
    inlined = next((h for h in hot if h.name == "inlined_func"), None)
    assert inlined is not None
    assert inlined.is_inlined is True

    normal = next((h for h in hot if h.name == "normal_func"), None)
    assert normal is None


def test_handles_missing_optional_fields():
    profile_data = {
        "meta": {},
        "threads": [
            {
                "name": "main",
                "isMainThread": True,
                "stringArray": ["func1"],
                "frameTable": {"length": 1, "func": [0], "inlineDepth": [0]},
                "funcTable": {"length": 1, "name": [0]},
                "stackTable": {"length": 1, "prefix": [None], "frame": [0]},
                "samples": {"length": 1, "stack": [0], "timeDeltas": [100]},
            }
        ],
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(profile_data, f)
        f.flush()
        profile = parse_gecko_profile(Path(f.name))

        assert profile.sample_count == 1
        hot = profile.hot_functions(1)
        assert len(hot) == 1


def test_aggregates_samples_across_threads():
    thread_a = {
        "name": "thread_a",
        "isMainThread": True,
        "stringArray": ["func_a", "func_b"],
        "frameTable": {"length": 2, "func": [0, 1], "inlineDepth": [0, 0]},
        "funcTable": {"length": 2, "name": [0, 1]},
        "stackTable": {
            "length": 2,
            "prefix": [None, 0],
            "frame": [0, 1],
        },
        "samples": {"length": 3, "stack": [1, 1, 1], "timeDeltas": [10, 10, 10]},
    }

    thread_b = {
        "name": "thread_b",
        "isMainThread": False,
        "stringArray": ["func_a", "func_c"],
        "frameTable": {"length": 2, "func": [0, 1], "inlineDepth": [0, 0]},
        "funcTable": {"length": 2, "name": [0, 1]},
        "stackTable": {
            "length": 2,
            "prefix": [None, 0],
            "frame": [0, 1],
        },
        "samples": {"length": 5, "stack": [1, 1, 1, 1, 1], "timeDeltas": [10, 10, 10, 10, 10]},
    }

    profile_data = {"meta": {}, "threads": [thread_a, thread_b]}
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(profile_data, f)
        f.flush()
        profile = parse_gecko_profile(Path(f.name))

        assert profile.sample_count == 8

        hot = profile.hot_functions(10)

        func_c = next((h for h in hot if h.name == "func_c"), None)
        assert func_c is not None
        assert func_c.self_pct == 100.0 * 5 / 8

        func_b = next((h for h in hot if h.name == "func_b"), None)
        assert func_b is not None
        assert func_b.self_pct == 100.0 * 3 / 8

        func_a = next((h for h in hot if h.name == "func_a"), None)
        assert func_a is None


def test_symbolization_with_real_profile():
    reference_profile = Path(__file__).parent.parent.parent / "reference" / "profile.json"
    if not reference_profile.exists():
        pytest.skip("reference/profile.json not found")

    profile = parse_gecko_profile(reference_profile)
    assert profile.sample_count > 0

    binary_path = profile.binary_path
    if binary_path is None or not binary_path.exists():
        pytest.skip("Binary path not found or doesn't exist")

    from samply_mcp.gecko.symbolizer import Symbolizer

    sym = Symbolizer(binary_path)
    profile_with_sym = parse_gecko_profile(reference_profile, symbolizer=sym)

    hot = profile_with_sym.hot_functions(20)
    assert len(hot) > 0

    resolved_names = [f.name for f in hot if not f.name.startswith("0x")]
    assert len(resolved_names) > 0, "Expected at least some resolved symbol names"

    top_func = hot[0]
    result = profile_with_sym.callers_of(top_func.name)
    assert result.matched_function.name == top_func.name
    assert isinstance(result.callers, list)
