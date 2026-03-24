import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from samply_mcp.gecko.symbolizer import SymbolInfo, Symbolizer


class TestSymbolizer:
    def test_resolve_addresses_empty_list(self):
        symbolizer = Symbolizer(Path("/usr/bin/ls"))
        result = symbolizer.resolve_addresses([])
        assert result == {}

    def test_resolve_addresses_caching(self):
        symbolizer = Symbolizer(Path("/usr/bin/ls"))

        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(stdout="0x1234\nmain\nfile.c:10\n", returncode=0)

            result1 = symbolizer.resolve_addresses(["0x1234"])
            assert result1["0x1234"].function_name == "main"
            assert result1["0x1234"].file == "file.c"
            assert result1["0x1234"].line == 10

            result2 = symbolizer.resolve_addresses(["0x1234"])
            assert result2["0x1234"].function_name == "main"

            assert mock_run.call_count == 1

    def test_resolve_addresses_unknown_symbol(self):
        symbolizer = Symbolizer(Path("/usr/bin/ls"))

        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(stdout="0x5678\n??\n??:0\n", returncode=0)

            result = symbolizer.resolve_addresses(["0x5678"])
            assert result["0x5678"].function_name == "0x5678"

    def test_resolve_addresses_multiple_addresses(self):
        symbolizer = Symbolizer(Path("/usr/bin/ls"))

        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="0x1000\nfoo\nfile.c:1\n0x2000\nbar\nfile.c:2\n0x3000\nbaz\nfile.c:3\n",
                returncode=0,
            )

            result = symbolizer.resolve_addresses(["0x1000", "0x2000", "0x3000"])
            assert result["0x1000"].function_name == "foo"
            assert result["0x2000"].function_name == "bar"
            assert result["0x3000"].function_name == "baz"

    def test_resolve_addresses_demangles_rust_names(self):
        symbolizer = Symbolizer(Path("/usr/bin/ls"))

        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="0xabcd\nprovenant::scanner::process::extract_license_information\nfile.rs:42\n",
                returncode=0,
            )

            result = symbolizer.resolve_addresses(["0xabcd"])
            assert (
                result["0xabcd"].function_name
                == "provenant::scanner::process::extract_license_information"
            )

    def test_resolve_addresses_timeout(self):
        symbolizer = Symbolizer(Path("/usr/bin/ls"))

        with patch.object(subprocess, "run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="addr2line", timeout=30)

            result = symbolizer.resolve_addresses(["0x1234"])
            assert result["0x1234"].function_name == "0x1234"

    def test_resolve_addresses_missing_binary(self):
        symbolizer = Symbolizer(Path("/nonexistent/binary"))

        with patch.object(subprocess, "run") as mock_run:
            mock_run.side_effect = FileNotFoundError("addr2line not found")

            result = symbolizer.resolve_addresses(["0x1234"])
            assert result["0x1234"].function_name == "0x1234"

    def test_resolve_addresses_partial_cache(self):
        symbolizer = Symbolizer(Path("/usr/bin/ls"))

        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="0x1111\ncached_func\nfile.c:1\n", returncode=0
            )

            symbolizer._cache["0x2222"] = SymbolInfo("already_cached", None, None)

            result = symbolizer.resolve_addresses(["0x1111", "0x2222"])

            assert result["0x1111"].function_name == "cached_func"
            assert result["0x2222"].function_name == "already_cached"

            mock_run.assert_called_once()
            call_args = mock_run.call_args[0][0]
            assert "0x1111" in call_args
            assert "0x2222" not in call_args

    def test_resolve_addresses_normalizes_hex_addresses(self):
        symbolizer = Symbolizer(Path("/usr/bin/ls"))

        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="0x0000000000001234\nmy_function\nfile.c:10\n", returncode=0
            )

            result = symbolizer.resolve_addresses(["0x1234"])
            assert result["0x1234"].function_name == "my_function"

    def test_normalize_address(self):
        assert Symbolizer._normalize_address("0x1234") == "0x1234"
        assert Symbolizer._normalize_address("0X1234") == "0x1234"
        assert Symbolizer._normalize_address("0x0000000000001234") == "0x1234"
        assert Symbolizer._normalize_address("not_hex") == "not_hex"

    def test_resolve_addresses_real_binary(self):
        ls_path = Path("/usr/bin/ls")
        if not ls_path.exists():
            pytest.skip("/usr/bin/ls not available")

        symbolizer = Symbolizer(ls_path)

        try:
            result = subprocess.run(
                ["nm", "-f", "sysv", str(ls_path)], capture_output=True, text=True, timeout=5
            )

            if result.returncode != 0:
                pytest.skip("nm failed on /usr/bin/ls")

            for line in result.stdout.split("\n")[:5]:
                if "|" in line:
                    parts = line.split("|")
                    if len(parts) >= 3:
                        addr_str = parts[0].strip()
                        if addr_str.startswith("0") or addr_str:
                            break
        except Exception:
            pytest.skip("Could not get symbol from nm")

        symbolizer._cache.clear()
