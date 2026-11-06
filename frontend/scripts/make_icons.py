"""Generate placeholder PWA icons (192/512 PNG) without external deps."""
import pathlib
import struct
import zlib

OUT = pathlib.Path(__file__).resolve().parents[1] / "public"


def png(n: int, path: pathlib.Path) -> None:
    rows = []
    for y in range(n):
        row = bytearray([0])
        for x in range(n):
            inside = (x - n / 2) ** 2 + (y - n / 2) ** 2 < (n * 0.42) ** 2
            bar = n * 0.3 < y < n * 0.4 or n * 0.6 < y < n * 0.7
            row += bytes((255, 255, 255) if inside and bar else (47, 111, 79))
        rows.append(bytes(row))

    def ch(t: bytes, d: bytes) -> bytes:
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    data = (b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", n, n, 8, 2, 0, 0, 0))
            + ch(b"IDAT", zlib.compress(b"".join(rows), 9)) + ch(b"IEND", b""))
    path.write_bytes(data)


if __name__ == "__main__":
    png(192, OUT / "icon-192.png")
    png(512, OUT / "icon-512.png")
