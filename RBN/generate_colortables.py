import argparse
import os
from typing import List, Tuple

BAND_PALETTE: List[Tuple[int, int, int]] = [
    (128,   0, 128),  # 0: 160m (Deep Violet)
    ( 65, 105, 225),  # 1: 80m  (Royal Blue)
    (  0, 191, 255),  # 2: 60m  (Deep Sky Blue)
    (  0, 210, 140),  # 3: 40m  (Emerald)
    ( 50, 205,  50),  # 4: 30m  (Lime Green)
    (255, 215,   0),  # 5: 20m  (Gold)
    (255, 140,   0),  # 6: 17m  (Dark Orange)
    (255,  69,   0),  # 7: 15m  (Orange-Red)
    (220,  20,  60),  # 8: 12m  (Crimson)
    (255,   0, 255),  # 9: 10m  (Magenta)
    (255, 182, 193),  # 10: 6m  (Light Pink)
    (255, 255, 255),  # 11: 2m  (White)
]

GRADIENT_STOPS: List[Tuple[float, Tuple[int, int, int]]] = [
    (0.00, ( 30,  60, 220)),
    (0.25, (  0, 200, 180)),
    (0.50, ( 80, 230,  50)),
    (0.75, (255, 165,   0)),
    (1.00, (255,  30,  60)),
]


def lerp_color(stops: List[Tuple[float, Tuple[int, int, int]]], t: float) -> Tuple[int, int, int]:
    for i in range(len(stops) - 1):
        t0, c0 = stops[i]
        t1, c1 = stops[i + 1]
        if t0 <= t <= t1:
            f = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
            return (
                int(round(c0[0] + f * (c1[0] - c0[0]))),
                int(round(c0[1] + f * (c1[1] - c0[1]))),
                int(round(c0[2] + f * (c1[2] - c0[2]))),
            )
    return stops[-1][1]


def write_stepped_band_table(path: str, samples: int = 256) -> None:
    n_bands = len(BAND_PALETTE)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"width {samples}\nlower 0.0\nupper 1.0\n\n")
        for i in range(samples):
            pos = i / (samples - 1)
            band_idx = min(int(round(pos * (n_bands - 1))), n_bands - 1)
            r, g, b = BAND_PALETTE[band_idx]
            f.write(f"mappingkey  {pos:.5f}     {r:3d}   {g:3d}   {b:3d}   255\n")


def write_continuous_table(path: str, samples: int = 256) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"width {samples}\nlower 0.0\nupper 1.0\n\n")
        for i in range(samples):
            pos = i / (samples - 1)
            r, g, b = lerp_color(GRADIENT_STOPS, pos)
            f.write(f"mappingkey  {pos:.5f}     {r:3d}   {g:3d}   {b:3d}   255\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default="./output/colortables")
    args = parser.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    write_stepped_band_table(os.path.join(args.out_dir, "rbn-band.txt"))
    for name in ("rbn-freq.txt", "rbn-snr.txt", "rbn-speed.txt", "rbn-distance.txt"):
        write_continuous_table(os.path.join(args.out_dir, name))
    print(f"Wrote 5 OpenSpace color tables to {args.out_dir}")


if __name__ == "__main__":
    main()