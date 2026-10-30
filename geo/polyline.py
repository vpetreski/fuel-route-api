"""Encoded polyline format (used by Valhalla with precision 6)."""


def decode(encoded: str, precision: int = 6) -> list[tuple[float, float]]:
    """Return [(lat, lon), ...] for an encoded polyline string."""
    factor = 10**precision
    coordinates = []
    index = lat = lon = 0
    length = len(encoded)
    while index < length:
        deltas = []
        for _ in range(2):
            shift = result = 0
            while True:
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            deltas.append(~(result >> 1) if result & 1 else result >> 1)
        lat += deltas[0]
        lon += deltas[1]
        coordinates.append((lat / factor, lon / factor))
    return coordinates
