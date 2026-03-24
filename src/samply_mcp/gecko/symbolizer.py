import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class SymbolInfo:
    function_name: str
    file: str | None
    line: int | None


class Symbolizer:
    def __init__(self, binary_path: Path):
        self.binary_path = binary_path
        self._cache: dict[str, SymbolInfo] = {}

    @staticmethod
    def _normalize_address(addr: str) -> str:
        if addr.startswith("0x") or addr.startswith("0X"):
            return hex(int(addr, 16))
        return addr

    def resolve_addresses(self, addresses: list[str]) -> dict[str, SymbolInfo]:
        if not addresses:
            return {}

        uncached = [a for a in addresses if a not in self._cache]
        if not uncached:
            return {a: self._cache.get(a, SymbolInfo(a, None, None)) for a in addresses}

        try:
            result = subprocess.run(
                ["addr2line", "-e", str(self.binary_path), "-f", "-a", "-C"] + uncached,
                capture_output=True,
                text=True,
                timeout=30,
            )

            lines = result.stdout.strip().split("\n")
            current_addr = None
            current_func = None

            for line in lines:
                if line.startswith("0x") or line.startswith("0X"):
                    current_addr = self._normalize_address(line)
                    current_func = None
                elif current_addr is not None and current_func is None:
                    current_func = line if line != "??" else None
                elif current_addr is not None and current_func is not None:
                    file_path = None
                    line_num = None
                    if line != "??:0" and line != "??":
                        parts = line.rsplit(":", 1)
                        if len(parts) == 2:
                            file_path = parts[0] if parts[0] != "??" else None
                            try:
                                line_num = int(parts[1])
                            except ValueError:
                                pass

                    self._cache[current_addr] = SymbolInfo(
                        function_name=current_func or current_addr,
                        file=file_path,
                        line=line_num,
                    )
                    current_addr = None
                    current_func = None

            result_map = {}
            for a in addresses:
                normalized = self._normalize_address(a)
                if normalized in self._cache:
                    result_map[a] = self._cache[normalized]
                else:
                    result_map[a] = SymbolInfo(a, None, None)
            return result_map

        except subprocess.TimeoutExpired:
            logger.error(f"addr2line timed out for {self.binary_path}")
            return {a: SymbolInfo(a, None, None) for a in addresses}
        except Exception as e:
            logger.error(f"addr2line failed: {e}")
            return {a: SymbolInfo(a, None, None) for a in addresses}
