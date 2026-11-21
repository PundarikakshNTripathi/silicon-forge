#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Independent protocol decoders (UART, SPI, I2C, WS2812) over pin traces, plus reference encoders to test them with.

Why independent: a protocol engine that drives its pins wrongly will happily agree with a decoder written from the same
misunderstanding. These decoders know nothing about any RTL; they only see edges on wires, the way a logic analyser or the
other end of the cable would. A "signal" is a sorted list of (time, level) edges meaning "level from this time on".

    sf_protocols.py uart DUMP.vcd --signal tb.uo_out[0] --bit-time 8680 [--parity even] [--out frames.json]
    sf_protocols.py spi  DUMP.vcd --sclk tb.uo_out[0] --mosi tb.uo_out[1] --cs tb.uo_out[3] [--mode 0]
    sf_protocols.py i2c  DUMP.vcd --scl tb.uio_out[0] --sda tb.uio_out[1]

Times are in the dump's units (VCD: picoseconds after scaling by $timescale). Standard library only.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

Edges = list[tuple[int, int]]


# ------------------------------------------------------------------------------------------------ signals

def level_at(edges: Edges, t: float, default: int = 1) -> int:
    lv = default
    for et, v in edges:
        if et > t:
            break
        lv = v
    return lv


def bisect_level(edges: Edges, times: list[int], t: float, default: int = 1) -> int:
    """level_at for sorted `times` (the first element of each edge), O(log n)."""
    import bisect
    i = bisect.bisect_right(times, t) - 1
    return edges[i][1] if i >= 0 else default


def _unit_ps(text: str) -> float:
    m = re.match(r"(\d+)\s*(fs|ps|ns|us|ms|s)", text.strip())
    if not m:
        return 1.0
    return int(m.group(1)) * {"fs": 1e-3, "ps": 1.0, "ns": 1e3, "us": 1e6, "ms": 1e9, "s": 1e12}[m.group(2)]


def _lines(path: Path) -> Iterable[str]:
    if path.suffix == ".fst":
        exe = shutil.which("fst2vcd")
        if exe is None:
            raise SystemExit("fst2vcd is needed to read FST dumps")
        proc = subprocess.Popen([exe, str(path)], stdout=subprocess.PIPE, text=True, errors="replace")
        assert proc.stdout is not None
        yield from proc.stdout
        proc.wait()
    else:
        with path.open(encoding="utf-8", errors="replace") as f:
            yield from f


def load_signals(path: str | Path, names: list[str]) -> dict[str, Edges]:
    """Edges (time in ps) for hierarchical names like `tb.uo_out[3]` (a bit of a vector) or `tb.clk`."""
    want: dict[str, tuple[str, int | None]] = {}
    for n in names:
        m = re.fullmatch(r"(.+?)\[(\d+)\]", n)
        want[n] = (m.group(1), int(m.group(2))) if m else (n, None)
    stack: list[str] = []
    by_id: dict[str, list[tuple[str, int | None, int]]] = {}  # id -> [(request, bit, width)]
    out: dict[str, Edges] = {n: [] for n in names}
    scale, in_ts, in_defs = 1.0, False, True
    t = 0
    last: dict[tuple[str, str], int] = {}
    for raw in _lines(Path(path)):
        line = raw.strip()
        if in_defs:
            if line.startswith("$timescale"):
                in_ts = True
                rest = line.replace("$timescale", "").replace("$end", "").strip()
                if rest:
                    scale, in_ts = _unit_ps(rest), False
            elif in_ts and line and not line.startswith("$"):
                scale, in_ts = _unit_ps(line), False
            elif line.startswith("$scope"):
                stack.append(line.split()[2])
            elif line.startswith("$upscope"):
                if stack:
                    stack.pop()
            elif line.startswith("$var"):
                p = line.split()
                full = ".".join(stack + [p[4]])
                for req, (base, bit) in want.items():
                    if full == base:
                        by_id.setdefault(p[3], []).append((req, bit, int(p[2])))
            elif line.startswith("$enddefinitions"):
                in_defs = False
            continue
        if not line:
            continue
        c = line[0]
        if c == "#":
            t = int(round(int(line[1:]) * scale))
        elif c in "bB":
            val, _, ident = line[1:].partition(" ")
            for req, bit, width in by_id.get(ident.strip(), []):
                v = val.rjust(width, "0")
                lv = v[width - 1 - (bit or 0)] if width > 1 else v[-1]
                _push(out, last, req, ident.strip(), t, lv)
        elif c in "01xXzZ":
            for req, _bit, _width in by_id.get(line[1:], []):
                _push(out, last, req, line[1:], t, c)
    return out


def _push(out, last, req, ident, t, lv) -> None:
    if lv not in "01":
        return
    v = int(lv)
    if last.get((req, ident)) != v:
        last[(req, ident)] = v
        out[req].append((t, v))


# ------------------------------------------------------------------------------------------------ decoders

def uart(edges: Edges, bit_time: float, nbits: int = 8, parity: str = "none", stop: int = 1,
         lsb_first: bool = True) -> list[dict]:
    """Decode asynchronous frames. Samples each bit mid-way; reports framing/parity errors and how far the real edges sit
    from the ideal bit grid (`max_edge_error`, in bit times), which is what bit-banged timing bugs show up as."""
    times = [e[0] for e in edges]
    frames: list[dict] = []
    i = 0
    while i < len(edges):
        t, v = edges[i]
        if v != 0 or (i > 0 and edges[i - 1][1] != 1):
            i += 1
            continue
        start = t
        if bisect_level(edges, times, start + bit_time / 2) != 0:
            i += 1
            continue  # a glitch, not a start bit
        bits = [bisect_level(edges, times, start + (1.5 + k) * bit_time) for k in range(nbits)]
        pos = nbits
        par_ok = True
        if parity != "none":
            p = bisect_level(edges, times, start + (1.5 + pos) * bit_time)
            ones = sum(bits) + p
            par_ok = (ones % 2 == 0) if parity == "even" else (ones % 2 == 1)
            pos += 1
        stops = [bisect_level(edges, times, start + (1.5 + pos + k) * bit_time) for k in range(stop)]
        order = bits if lsb_first else bits[::-1]
        byte = sum(b << k for k, b in enumerate(order))
        grid_err = 0.0
        for et, _ in edges[i:]:
            if et > start + (1 + pos + stop) * bit_time:
                break
            off = (et - start) / bit_time
            grid_err = max(grid_err, abs(off - round(off)))
        frames.append({"t": start, "byte": byte, "framing_ok": all(s == 1 for s in stops), "parity_ok": par_ok,
                       "max_edge_error": round(grid_err, 4)})
        end = start + (1 + pos + stop) * bit_time - bit_time / 2
        while i < len(edges) and edges[i][0] < end:
            i += 1
    return frames


def spi(sclk: Edges, mosi: Edges, miso: Edges | None, cs: Edges, mode: int = 0, nbits: int = 8,
        lsb_first: bool = False) -> list[dict]:
    """Decode chip-select-framed SPI transfers (cs active low). Modes: 0/3 sample on the rising edge, 1/2 on the falling."""
    cpol, cpha = (mode >> 1) & 1, mode & 1
    sample_rising = (cpol == 0) == (cpha == 0)  # CPOL^CPHA == 0 -> rising
    st, mt = [e[0] for e in sclk], [e[0] for e in mosi]
    ot = [e[0] for e in miso] if miso else []
    out: list[dict] = []
    windows: list[tuple[int, int]] = []
    start = None
    for t, v in cs:
        if v == 0 and start is None:
            start = t
        elif v == 1 and start is not None:
            windows.append((start, t))
            start = None
    if start is not None:
        windows.append((start, sclk[-1][0] + 1 if sclk else start + 1))
    for a, b in windows:
        picks = [t for t, v in sclk if a <= t < b and v == (1 if sample_rising else 0)
                 and bisect_level(sclk, st, t - 1, cpol) != v]
        mo_bits = [bisect_level(mosi, mt, t) for t in picks]
        mi_bits = [bisect_level(miso, ot, t) for t in picks] if miso else []

        def bytes_of(bits: list[int]) -> list[int]:
            res = []
            for k in range(0, len(bits) - nbits + 1, nbits):
                chunk = bits[k:k + nbits]
                chunk = chunk[::-1] if not lsb_first else chunk
                res.append(sum(bit << i for i, bit in enumerate(chunk)))
            return res

        out.append({"t": a, "bits": len(picks), "mosi": bytes_of(mo_bits), "miso": bytes_of(mi_bits),
                    "complete": len(picks) % nbits == 0 and bool(picks)})
    return out


def i2c(scl: Edges, sda: Edges) -> list[dict]:
    """Decode an I2C bus into START / address / data / STOP events with ACK status."""
    ct, dt = [e[0] for e in scl], [e[0] for e in sda]
    times = sorted({t for t, _ in scl} | {t for t, _ in sda})
    events: list[dict] = []
    in_xfer = False
    bits: list[int] = []
    first_byte = True
    prev_c, prev_d = 1, 1
    for t in times:
        c, d = bisect_level(scl, ct, t), bisect_level(sda, dt, t)
        if prev_c == 1 and c == 1 and prev_d == 1 and d == 0:  # SDA falls while SCL is high
            events.append({"t": t, "type": "start" if not in_xfer else "repeated_start"})
            in_xfer, bits, first_byte = True, [], True
        elif prev_c == 1 and c == 1 and prev_d == 0 and d == 1 and in_xfer:  # SDA rises while SCL is high
            events.append({"t": t, "type": "stop"})
            in_xfer, bits = False, []
        elif in_xfer and prev_c == 0 and c == 1:  # SCL rising: sample SDA
            bits.append(d)
            if len(bits) == 9:
                val = sum(b << (7 - k) for k, b in enumerate(bits[:8]))
                ev = {"t": t, "type": "byte", "value": val, "ack": bits[8] == 0}
                if first_byte:
                    ev.update(type="address", address=val >> 1, read=bool(val & 1))
                    first_byte = False
                events.append(ev)
                bits = []
        prev_c, prev_d = c, d
    return events


def ws2812(edges: Edges, bit_threshold: float, reset_time: float) -> list[int]:
    """Decode a WS2812 stream into 24-bit GRB words: a high pulse longer than `bit_threshold` is a 1."""
    words: list[int] = []
    cur: list[int] = []
    rise = None
    last_fall = None
    for t, v in edges:
        if v == 1:
            if last_fall is not None and t - last_fall >= reset_time:
                cur = []  # a latch gap discards a partial pixel
            rise = t
        elif rise is not None:
            cur.append(1 if t - rise >= bit_threshold else 0)
            last_fall = t
            if len(cur) == 24:
                words.append(sum(b << (23 - k) for k, b in enumerate(cur)))
                cur = []
            rise = None
    return words


# ------------------------------------------------------------------------------------------------ encoders

def enc_uart(data: bytes, bit_time: int, start_at: int = 0, parity: str = "none", stop: int = 1, idle: int = 0,
             lsb_first: bool = True) -> Edges:
    edges: Edges = [(0, 1)]
    t = start_at

    def put(level: int, dur: int) -> None:
        nonlocal t
        if edges[-1][1] != level:
            edges.append((t, level))
        t += dur

    for byte in data:
        put(0, bit_time)
        bits = [(byte >> k) & 1 for k in (range(8) if lsb_first else range(7, -1, -1))]
        for b in bits:
            put(b, bit_time)
        if parity != "none":
            ones = sum(bits)
            put((ones % 2) if parity == "even" else 1 - (ones % 2), bit_time)
        put(1, bit_time * stop + idle)
    return edges


def enc_spi(mosi_bytes: bytes, period: int, mode: int = 0, start_at: int = 100) -> tuple[Edges, Edges, Edges]:
    """(sclk, mosi, cs) for one transfer, MSB first."""
    cpol, cpha = (mode >> 1) & 1, mode & 1
    sclk: Edges = [(0, cpol)]
    mosi: Edges = [(0, 0)]
    cs: Edges = [(0, 1), (start_at, 0)]
    t = start_at + period
    half = period // 2

    def drive(edges: Edges, tt: int, v: int) -> None:
        if edges[-1][1] != v:
            edges.append((tt, v))

    for byte in mosi_bytes:
        for k in range(7, -1, -1):
            bit = (byte >> k) & 1
            if cpha == 0:  # data valid before the first edge; sample on the first edge
                drive(mosi, t, bit)
                drive(sclk, t + half, 1 - cpol)
                drive(sclk, t + period, cpol)
            else:  # data changes on the first edge; sample on the second
                drive(sclk, t, 1 - cpol)
                drive(mosi, t, bit)
                drive(sclk, t + half, cpol)
            t += period
    cs.append((t + period, 1))
    return sclk, mosi, cs


def enc_i2c(addr: int, data: bytes, read: bool = False, period: int = 100, nack_at: int | None = None) -> tuple[Edges, Edges]:
    scl: Edges = [(0, 1)]
    sda: Edges = [(0, 1)]
    t = period

    def put(edges: Edges, tt: int, v: int) -> None:
        if edges[-1][1] != v:
            edges.append((tt, v))

    put(sda, t, 0)  # START
    t += period // 2
    put(scl, t, 0)
    t += period // 2
    seq = [((addr << 1) | int(read))] + list(data)
    for idx, byte in enumerate(seq):
        for k in range(7, -1, -1):
            put(sda, t, (byte >> k) & 1)
            t += period // 4
            put(scl, t, 1)
            t += period // 2
            put(scl, t, 0)
            t += period // 4
        put(sda, t, 1 if nack_at == idx else 0)  # ACK slot driven low by the target
        t += period // 4
        put(scl, t, 1)
        t += period // 2
        put(scl, t, 0)
        t += period // 4
    put(sda, t, 0)
    t += period // 4
    put(scl, t, 1)
    t += period // 4
    put(sda, t, 1)  # STOP
    return scl, sda


def enc_ws2812(pixels: list[int], t0h: int = 350, t1h: int = 700, period: int = 1250, reset: int = 60000) -> Edges:
    edges: Edges = [(0, 0)]
    t = 100
    for px in pixels:
        for k in range(23, -1, -1):
            hi = t1h if (px >> k) & 1 else t0h
            edges += [(t, 1), (t + hi, 0)]
            t += period
    edges.append((t + reset, 0))
    return edges


# ------------------------------------------------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kind", choices=["uart", "spi", "i2c"])
    ap.add_argument("dump")
    ap.add_argument("--signal")
    ap.add_argument("--bit-time", type=float, help="uart: bit time in the dump's ps")
    ap.add_argument("--parity", default="none", choices=["none", "even", "odd"])
    ap.add_argument("--sclk")
    ap.add_argument("--mosi")
    ap.add_argument("--miso")
    ap.add_argument("--cs")
    ap.add_argument("--mode", type=int, default=0)
    ap.add_argument("--scl")
    ap.add_argument("--sda")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    if a.kind == "uart":
        sig = load_signals(a.dump, [a.signal])
        res: object = uart(sig[a.signal], a.bit_time, parity=a.parity)
    elif a.kind == "spi":
        names = [n for n in (a.sclk, a.mosi, a.miso, a.cs) if n]
        sig = load_signals(a.dump, names)
        res = spi(sig[a.sclk], sig[a.mosi], sig[a.miso] if a.miso else None, sig[a.cs], a.mode)
    else:
        sig = load_signals(a.dump, [a.scl, a.sda])
        res = i2c(sig[a.scl], sig[a.sda])
    text = json.dumps(res, indent=2)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    print(text if not a.out else f"{len(res) if isinstance(res, list) else 1} item(s) -> {a.out}")  # type: ignore[arg-type]
    return 0


if __name__ == "__main__":
    sys.exit(main())
