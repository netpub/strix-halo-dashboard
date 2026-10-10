from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from halo_dash.textio import read_float_file, read_int_file, read_text_file


@dataclass
class GpuSample:
    name: str
    busy_percent: float | None
    gtt_used: int
    gtt_total: int
    vram_used: int
    vram_total: int
    temp_c: float | None
    power_w: float | None
    gfx_clock_mhz: float | None


@dataclass
class HostSample:
    cpu_percent: float
    cpu_cores: int
    cpu_threads: int
    ram_used: int
    ram_total: int
    disk_read_bps: float
    disk_write_bps: float
    gpu: GpuSample | None


_SKIP_DISK_PREFIXES = ("loop", "ram", "fd", "sr", "zram", "dm-", "zd", "nbd")


class HostReader:
    def __init__(self, sys_root: Path = Path("/sys"), proc_root: Path = Path("/proc")):
        self.sys_root = sys_root
        self.proc_root = proc_root
        self._cpu_prev: tuple[int, int] | None = None
        self._disk_prev: tuple[int, int, float] | None = None
        self._cpu_layout: tuple[int, int] | None = None

    def sample(self, now: float) -> HostSample:
        cpu = self._cpu()
        ram_used, ram_total = self._ram()
        read_bps, write_bps = self._disk(now)
        cores, threads = self._cores()
        return HostSample(
            cpu_percent=cpu,
            cpu_cores=cores,
            cpu_threads=threads,
            ram_used=ram_used,
            ram_total=ram_total,
            disk_read_bps=read_bps,
            disk_write_bps=write_bps,
            gpu=self._gpu(),
        )

    def _cpu(self) -> float:
        stat = read_text_file(self.proc_root / "stat")
        if not stat:
            return 0.0
        line = next((row for row in stat.splitlines() if row.startswith("cpu ")), "")
        parts = [int(part) for part in line.split()[1:] if part.isdigit()]
        if len(parts) < 4:
            return 0.0
        idle = parts[3] + (parts[4] if len(parts) > 4 else 0)
        total = sum(parts[:8]) if len(parts) >= 8 else sum(parts)
        if self._cpu_prev is None:
            self._cpu_prev = (total, idle)
            return 0.0
        prev_total, prev_idle = self._cpu_prev
        self._cpu_prev = (total, idle)
        delta_total = total - prev_total
        delta_idle = idle - prev_idle
        if delta_total <= 0:
            return 0.0
        return max(0.0, min(100.0, (delta_total - delta_idle) * 100.0 / delta_total))

    def _ram(self) -> tuple[int, int]:
        text = read_text_file(self.proc_root / "meminfo") or ""
        values: dict[str, int] = {}
        for line in text.splitlines():
            key, _, rest = line.partition(":")
            number = rest.strip().split()
            if number and number[0].isdigit():
                values[key] = int(number[0]) * 1024
        total = values.get("MemTotal", 0)
        available = values.get("MemAvailable", 0)
        used = max(0, total - available)
        return used, total

    def _disk(self, now: float) -> tuple[float, float]:
        text = read_text_file(self.proc_root / "diskstats") or ""
        read_sectors = 0
        write_sectors = 0
        for line in text.splitlines():
            parts = line.split()
            if len(parts) < 10:
                continue
            name = parts[2]
            if name.startswith(_SKIP_DISK_PREFIXES):
                continue
            read_sectors += int(parts[5])
            write_sectors += int(parts[9])
        read_bytes = read_sectors * 512
        write_bytes = write_sectors * 512
        if self._disk_prev is None:
            self._disk_prev = (read_bytes, write_bytes, now)
            return 0.0, 0.0
        prev_read, prev_write, prev_time = self._disk_prev
        self._disk_prev = (read_bytes, write_bytes, now)
        elapsed = now - prev_time
        if elapsed <= 0:
            return 0.0, 0.0
        return max(0.0, (read_bytes - prev_read) / elapsed), max(0.0, (write_bytes - prev_write) / elapsed)

    def _cores(self) -> tuple[int, int]:
        if self._cpu_layout is not None:
            return self._cpu_layout
        text = read_text_file(self.proc_root / "cpuinfo") or ""
        threads = 0
        cores: set[tuple[str, str]] = set()
        physical = ""
        core = ""
        seen_processor = False
        for line in text.splitlines():
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            if key == "processor":
                if seen_processor:
                    cores.add((physical, core or str(threads)))
                seen_processor = True
                threads += 1
                physical = ""
                core = ""
            elif key == "physical id":
                physical = value
            elif key == "core id":
                core = value
        if seen_processor:
            cores.add((physical, core or str(threads)))
        logical = threads or 1
        physical_cores = len(cores) or logical
        self._cpu_layout = (physical_cores, logical)
        return self._cpu_layout

    def _gpu(self) -> GpuSample | None:
        card = find_gpu_card(self.sys_root)
        if card is None:
            return None
        device = card / "device"
        busy = read_float_file(device / "gpu_busy_percent")
        name = (
            read_text_file(device / "product_name")
            or read_text_file(card / "device" / "board_name")
            or "AMD Radeon Graphics"
        )
        temp_c, power_w, clock = _read_hwmon(device)
        return GpuSample(
            name=name,
            busy_percent=busy,
            gtt_used=read_int_file(device / "mem_info_gtt_used") or 0,
            gtt_total=read_int_file(device / "mem_info_gtt_total") or 0,
            vram_used=read_int_file(device / "mem_info_vram_used") or 0,
            vram_total=read_int_file(device / "mem_info_vram_total") or 0,
            temp_c=temp_c,
            power_w=power_w,
            gfx_clock_mhz=clock,
        )


def find_gpu_card(sys_root: Path) -> Path | None:
    drm = sys_root / "class" / "drm"
    if not drm.is_dir():
        return None
    found: list[tuple[bool, str, Path]] = []
    for card in drm.iterdir():
        if not card.name.startswith("card") or not card.name[4:].isdigit():
            continue
        device = card / "device"
        if not (device / "gpu_busy_percent").is_file():
            continue
        vendor = (read_text_file(device / "vendor") or "").lower()
        found.append((vendor == "0x1002", card.name, card))
    if not found:
        return None
    found.sort(key=lambda item: (not item[0], item[1]))
    return found[0][2]


def _read_hwmon(device: Path) -> tuple[float | None, float | None, float | None]:
    hwmon_root = device / "hwmon"
    if not hwmon_root.is_dir():
        return None, None, None
    temp_c = None
    power_w = None
    clock = None
    for hwmon in sorted(hwmon_root.glob("hwmon*")):
        edge = _temp_by_label(hwmon, "edge")
        if edge is None:
            edge = read_int_file(hwmon / "temp1_input")
        if edge is not None:
            temp_c = edge / 1000.0
        power = read_int_file(hwmon / "power1_average")
        if power is None:
            power = read_int_file(hwmon / "power1_input")
        if power is not None:
            power_w = power / 1_000_000.0
        freq = read_int_file(hwmon / "freq1_input")
        if freq is not None:
            clock = freq / 1_000_000.0
    return temp_c, power_w, clock


def _temp_by_label(hwmon: Path, label: str) -> int | None:
    for path in sorted(hwmon.glob("temp*_label")):
        text = read_text_file(path) or ""
        if label not in text.lower():
            continue
        index = path.name.removeprefix("temp").removesuffix("_label")
        return read_int_file(hwmon / f"temp{index}_input")
    return None
