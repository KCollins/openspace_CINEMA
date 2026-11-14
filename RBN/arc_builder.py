import struct
from datetime import datetime, timezone
from typing import List, Tuple, Optional
import numpy as np

R_EARTH_M = 6371000.0
J2000_EPOCH = datetime(2000, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

# Canonical Amateur Bands: (band_name, band_index, min_khz, max_khz)
BAND_DEFINITIONS = [
    ("160m", 0,   1800.0,   2000.0),
    ("80m",  1,   3500.0,   4000.0),
    ("60m",  2,   5300.0,   5410.0),
    ("40m",  3,   7000.0,   7300.0),
    ("30m",  4,  10100.0,  10150.0),
    ("20m",  5,  14000.0,  14350.0),
    ("17m",  6,  18068.0,  18168.0),
    ("15m",  7,  21000.0,  21450.0),
    ("12m",  8,  24890.0,  24990.0),
    ("10m",  9,  28000.0,  29700.0),
    ("6m",  10,  50000.0,  54000.0),
    ("2m",  11, 144000.0, 148000.0),
]

BAND_NAME_TO_INDEX = {b[0]: float(b[1]) for b in BAND_DEFINITIONS}


def resolve_band_index(band_str: str, freq_khz: float) -> float:
    """Returns canonical band index (0.0 - 11.0) from band label or kHz frequency."""
    b = (band_str or "").strip().lower()
    if b in BAND_NAME_TO_INDEX:
        return BAND_NAME_TO_INDEX[b]
    for _, idx, f_min, f_max in BAND_DEFINITIONS:
        if f_min <= freq_khz <= f_max:
            return float(idx)
    return 5.0  # Default to 20m if out-of-band


def dt_to_j2000_seconds(dt: datetime) -> float:
    """Converts a timezone-aware UTC datetime to J2000 seconds."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (dt - J2000_EPOCH).total_seconds()


def build_propagation_arc(
    tx_lat: float,
    tx_lon: float,
    rx_lat: float,
    rx_lon: float,
    scalars: Tuple[float, float, float, float, float],
    num_pts: int = 36,
) -> Optional[Tuple[np.ndarray, float]]:
    """
    Generates an elevated great-circle arc from TX -> RX.
    Returns (vertices_Nx3_float32, great_circle_distance_km) or None for zero-length paths.
    """
    lat1, lon1 = np.radians(tx_lat), np.radians(tx_lon)
    lat2, lon2 = np.radians(rx_lat), np.radians(rx_lon)

    p1 = np.array([np.cos(lat1) * np.cos(lon1), np.cos(lat1) * np.sin(lon1), np.sin(lat1)], dtype=np.float64)
    p2 = np.array([np.cos(lat2) * np.cos(lon2), np.cos(lat2) * np.sin(lon2), np.sin(lat2)], dtype=np.float64)

    dot = float(np.clip(np.dot(p1, p2), -1.0, 1.0))
    omega = np.arccos(dot)
    distance_km = (omega * R_EARTH_M) / 1000.0

    # Skip local self-skimmer spots (< 5 km)
    if distance_km < 5.0:
        return None

    sin_omega = np.sin(omega)
    t = np.linspace(0.0, 1.0, num_pts, dtype=np.float64)

    if sin_omega < 1e-6:
        unit_vecs = np.repeat(p1[None, :], num_pts, axis=0)
    else:
        w1 = (np.sin((1.0 - t) * omega) / sin_omega)[:, None]
        w2 = (np.sin(t * omega) / sin_omega)[:, None]
        unit_vecs = w1 * p1[None, :] + w2 * p2[None, :]

    # Scale F2-layer apex height smoothly between 160 km (regional) and 380 km (long DX)
    apex_m = np.clip(160000.0 + (distance_km / 10000.0) * 220000.0, 160000.0, 380000.0)
    altitudes = 2000.0 + apex_m * np.sin(np.pi * t)
    radii = (R_EARTH_M + altitudes)[:, None]

    xyz = (unit_vecs * radii).astype(np.float32)
    return xyz, float(distance_km)


def export_arcs_to_osfls(
    osfls_path: str,
    arcs: List[Tuple[np.ndarray, Tuple[float, float, float, float, float]]],
    trigger_time_j2000: float,
) -> None:
    """
    Writes propagation arcs to OpenSpace OSFLS binary format with 5 scalar quantities:
      0: band_index   (0..11)
      1: freq_khz     (1800..148000)
      2: snr_db       (-10..60)
      3: speed_wpm    (5..60)
      4: distance_km  (0..20000)
    """
    scalar_names = ["band_index", "freq_khz", "snr_db", "speed_wpm", "distance_km"]
    n_extras = len(scalar_names)
    all_names_bytes = b"".join(name.encode("utf-8") + b"\0" for name in scalar_names)
    byte_size_all_names = len(all_names_bytes)

    n_lines = len(arcs)
    if n_lines == 0:
        # Write valid empty frame so OpenSpace clears arcs during quiet time bins
        with open(osfls_path, "wb") as ofs:
            ofs.write(struct.pack("i", 0))
            ofs.write(struct.pack("d", trigger_time_j2000))
            ofs.write(struct.pack("i", 0))
            ofs.write(struct.pack("B", 0))
            ofs.write(struct.pack("Q", 0))
            ofs.write(struct.pack("Q", 0))
            ofs.write(struct.pack("Q", n_extras))
            ofs.write(struct.pack("Q", byte_size_all_names))
            ofs.write(all_names_bytes)
        return

    line_starts = np.empty(n_lines, dtype=np.int32)
    line_counts = np.empty(n_lines, dtype=np.uint32)

    curr_idx = 0
    for i, (xyz, _) in enumerate(arcs):
        pts = xyz.shape[0]
        line_starts[i] = curr_idx
        line_counts[i] = pts
        curr_idx += pts

    n_points = curr_idx
    vertex_positions = np.vstack([xyz for xyz, _ in arcs]).astype(np.float32)

    extra_arrays = [np.empty(n_points, dtype=np.float32) for _ in range(n_extras)]
    offset = 0
    for xyz, scalars in arcs:
        pts = xyz.shape[0]
        for q_idx in range(n_extras):
            extra_arrays[q_idx][offset : offset + pts] = scalars[q_idx]
        offset += pts

    with open(osfls_path, "wb") as ofs:
        ofs.write(struct.pack("i", 0))                    # CurrentVersion = 0
        ofs.write(struct.pack("d", trigger_time_j2000))    # _triggerTime
        ofs.write(struct.pack("i", 0))                    # _model = Invalid/Generic (0)
        ofs.write(struct.pack("B", 0))                    # _isMorphable = 0
        ofs.write(struct.pack("Q", n_lines))              # nLines
        ofs.write(struct.pack("Q", n_points))             # nPoints
        ofs.write(struct.pack("Q", n_extras))             # nExtras
        ofs.write(struct.pack("Q", byte_size_all_names))  # byteSizeAllNames

        line_starts.tofile(ofs)
        line_counts.tofile(ofs)
        vertex_positions.tofile(ofs)
        for eq_arr in extra_arrays:
            eq_arr.tofile(ofs)
        ofs.write(all_names_bytes)