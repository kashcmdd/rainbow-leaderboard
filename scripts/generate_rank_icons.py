#!/usr/bin/env python3
"""Generate the rank icon set.

Every icon is a 64x64 two-tone badge: the tier supplies the silhouette and
colour, the level supplies one to five pips. No tiny text inside tier icons.
Top 1-10 use a medal with the placement numeral.

Run from the repo root: python3 scripts/generate_rank_icons.py
"""

from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "app" / "static" / "rank-icons"

TIERS = {
    "bronze": ["#7A4520", "#96541A", "#B8682A", "#CD7F32", "#E09A50"],
    "silver": ["#787878", "#909090", "#A8A8A8", "#C0C0C0", "#D8D8D8"],
    "gold": ["#B8960C", "#CCA800", "#FFD700", "#FFE033", "#FFEA70"],
    "platinum": ["#0088AA", "#00AACC", "#00C8E8", "#00E5FF", "#66F0FF"],
    "emerald": ["#007A30", "#009940", "#00BB50", "#00E676", "#33FF8A"],
    "diamond": ["#6600CC", "#7B35FF", "#9955FF", "#B388FF", "#CCAAFF"],
}

TOP_COLORS = {
    1: "#FFD700", 2: "#E8E8E8", 3: "#FF8C00", 4: "#AA00FF", 5: "#E040FB",
    6: "#F50057", 7: "#FF1744", 8: "#FF3D00", 9: "#FF5500", 10: "#FF6B00",
}

ROMAN = ["i", "ii", "iii", "iv", "v"]


def pips(level: int, color: str, y: int = 50) -> str:
    out = []
    for i in range(5):
        x = 32 + (i - 2) * 6
        filled = i < level
        out.append(
            f'<path d="M{x} {y-2.6} L{x+2.6} {y} L{x} {y+2.6} L{x-2.6} {y} Z" '
            f'fill="{color}" fill-opacity="{0.95 if filled else 0.12}" '
            f'stroke="{color}" stroke-opacity="{0.9 if filled else 0.35}" stroke-width="1"/>'
        )
    return "".join(out)


def badge(shape: str, color: str, level: int, y: int = 50) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
        f'{shape.replace("COLOR", color)}'
        f'{pips(level, color, y)}'
        "</svg>\n"
    )


def shield(color: str, level: int) -> str:
    return badge(
        '<path d="M32 4 L57 13 V31 C57 46 32 60 32 60 C32 60 7 46 7 31 V13 Z" '
        'fill="COLOR" fill-opacity="0.16" stroke="COLOR" stroke-width="2.6" stroke-linejoin="round"/>'
        '<path d="M32 12 L50 18 V31 C50 41 32 51 32 51 C32 51 14 41 14 31 V18 Z" '
        'fill="none" stroke="COLOR" stroke-opacity="0.45" stroke-width="1.4"/>',
        color, level,
    )


def shield_star(color: str, level: int) -> str:
    return badge(
        '<path d="M32 4 L57 13 V31 C57 46 32 60 32 60 C32 60 7 46 7 31 V13 Z" '
        'fill="COLOR" fill-opacity="0.16" stroke="COLOR" stroke-width="2.6" stroke-linejoin="round"/>'
        '<path d="M32 15 L35 23 L43 23 L36.5 28 L39 36 L32 31 L25 36 L27.5 28 L21 23 L29 23 Z" '
        'fill="COLOR" fill-opacity="0.75"/>',
        color, level, y=50,
    )


def shield_chevron(color: str, level: int) -> str:
    return badge(
        '<path d="M32 4 L57 13 V31 C57 46 32 60 32 60 C32 60 7 46 7 31 V13 Z" '
        'fill="COLOR" fill-opacity="0.16" stroke="COLOR" stroke-width="2.6" stroke-linejoin="round"/>'
        '<path d="M21 24 L32 31 L43 24" fill="none" stroke="COLOR" stroke-opacity="0.6" '
        'stroke-width="2.2" stroke-linecap="square"/>'
        '<path d="M21 31 L32 38 L43 31" fill="none" stroke="COLOR" stroke-opacity="0.3" '
        'stroke-width="1.6" stroke-linecap="square"/>',
        color, level,
    )


def platinum(color: str, level: int) -> str:
    return badge(
        '<path d="M32 4 L57 16 V38 L32 60 L7 38 V16 Z" '
        'fill="COLOR" fill-opacity="0.14" stroke="COLOR" stroke-width="2.6" stroke-linejoin="round"/>'
        '<path d="M32 12 L50 20 V36 L32 51 L14 36 V20 Z" '
        'fill="none" stroke="COLOR" stroke-opacity="0.45" stroke-width="1.4"/>',
        color, level, y=41,
    )


def emerald(color: str, level: int) -> str:
    return badge(
        '<path d="M32 4 L55 17 V47 L32 60 L9 47 V17 Z" '
        'fill="COLOR" fill-opacity="0.14" stroke="COLOR" stroke-width="2.6" stroke-linejoin="round"/>'
        '<path d="M32 12 L48 21 V43 L32 52 L16 43 V21 Z" '
        'fill="none" stroke="COLOR" stroke-opacity="0.45" stroke-width="1.4"/>',
        color, level, y=41,
    )


def diamond(color: str, level: int) -> str:
    return badge(
        '<path d="M32 3 L59 22 L32 61 L5 22 Z" '
        'fill="COLOR" fill-opacity="0.14" stroke="COLOR" stroke-width="2.6" stroke-linejoin="round"/>'
        '<path d="M32 3 L40 22 L32 61 L24 22 Z" fill="COLOR" fill-opacity="0.2"/>'
        '<path d="M5 22 L59 22" stroke="COLOR" stroke-opacity="0.45" stroke-width="1.4"/>',
        color, level, y=39,
    )


def champion(color: str, level: int = 0) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
        f'<path d="M8 44 L10 18 L24 29 L32 12 L40 29 L54 18 L56 44 Z" '
        f'fill="{color}" fill-opacity="0.2" stroke="{color}" stroke-width="2.6" stroke-linejoin="round"/>'
        f'<rect x="8" y="46" width="48" height="8" rx="1.5" fill="{color}" fill-opacity="0.35" '
        f'stroke="{color}" stroke-width="2"/>'
        f'<circle cx="10" cy="18" r="2.2" fill="{color}"/><circle cx="54" cy="18" r="2.2" fill="{color}"/>'
        f'<circle cx="32" cy="12" r="2.6" fill="{color}"/>'
        "</svg>\n"
    )


def top(place: int) -> str:
    color = TOP_COLORS[place]
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
        f'<path d="M24 4 L32 16 L40 4 L37 24 L27 24 Z" fill="{color}" fill-opacity="0.25" '
        f'stroke="{color}" stroke-width="1.8" stroke-linejoin="round"/>'
        f'<circle cx="32" cy="37" r="20" fill="{color}" fill-opacity="0.16" stroke="{color}" stroke-width="2.6"/>'
        f'<circle cx="32" cy="37" r="15" fill="none" stroke="{color}" stroke-opacity="0.45" stroke-width="1.4"/>'
        f'<text x="32" y="45" text-anchor="middle" fill="{color}" '
        f'font-family="Barlow Condensed, Arial Narrow, sans-serif" font-size="24" font-weight="700">{place}</text>'
        "</svg>\n"
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    builders = {
        "bronze": shield,
        "silver": shield_chevron,
        "gold": shield_star,
        "platinum": platinum,
        "emerald": emerald,
        "diamond": diamond,
    }
    for tier, colors in TIERS.items():
        for level, color in enumerate(colors, 1):
            (OUT / f"{tier}_{ROMAN[level - 1]}.svg").write_text(
                builders[tier](color, level)
            )
    (OUT / "champion.svg").write_text(champion("#E8197D"))
    for place in range(1, 11):
        (OUT / f"top_{place}.svg").write_text(top(place))
    print(f"wrote {len(TIERS) * 5 + 11} icons to {OUT}")


if __name__ == "__main__":
    main()
