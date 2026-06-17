RANKS = [
    ("Bronze I", 0, "#7A4520"),
    ("Bronze II", 100, "#96541A"),
    ("Bronze III", 200, "#B8682A"),
    ("Bronze IV", 300, "#CD7F32"),
    ("Bronze V", 400, "#E09A50"),
    ("Silver I", 500, "#787878"),
    ("Silver II", 600, "#909090"),
    ("Silver III", 700, "#A8A8A8"),
    ("Silver IV", 800, "#C0C0C0"),
    ("Silver V", 900, "#D8D8D8"),
    ("Gold I", 1000, "#B8960C"),
    ("Gold II", 1100, "#CCA800"),
    ("Gold III", 1200, "#FFD700"),
    ("Gold IV", 1300, "#FFE033"),
    ("Gold V", 1400, "#FFEA70"),
    ("Platinum I", 1500, "#0088AA"),
    ("Platinum II", 1600, "#00AACC"),
    ("Platinum III", 1700, "#00C8E8"),
    ("Platinum IV", 1800, "#00E5FF"),
    ("Platinum V", 1900, "#66F0FF"),
    ("Emerald I", 2000, "#007A30"),
    ("Emerald II", 2100, "#009940"),
    ("Emerald III", 2200, "#00BB50"),
    ("Emerald IV", 2300, "#00E676"),
    ("Emerald V", 2400, "#33FF8A"),
    ("Diamond I", 2500, "#6600CC"),
    ("Diamond II", 2600, "#7B35FF"),
    ("Diamond III", 2700, "#9955FF"),
    ("Diamond IV", 2800, "#B388FF"),
    ("Diamond V", 2900, "#CCAAFF"),
    ("Champion", 3000, "#E8197D"),
]

TOP_RANK_MIN_ELO = RANKS[-1][1]

TOP_RANKS = [
    ("Top 10", "#FF6B00"),
    ("Top 9", "#FF5500"),
    ("Top 8", "#FF3D00"),
    ("Top 7", "#FF1744"),
    ("Top 6", "#F50057"),
    ("Top 5", "#E040FB"),
    ("Top 4", "#AA00FF"),
    ("Top 3", "#FF8C00"),
    ("Top 2", "#E8E8E8"),
    ("Top 1", "#FFD700"),
]


def get_rank(elo: int, top_position: int = None) -> tuple[str, str]:
    if top_position is not None and 1 <= top_position <= 10 and elo >= TOP_RANK_MIN_ELO:
        name, color = TOP_RANKS[10 - top_position]
        return name, color
    for name, threshold, color in reversed(RANKS):
        if elo >= threshold:
            return name, color
    return "Unranked", "#666666"


async def recalculate_top_positions(db, format: str = "1v1"):
    """
    Recalculate exclusive top 10 positions.
    Only players at or above TOP_RANK_MIN_ELO are eligible for exclusive slots.
    Ranks 10 through 1 are exclusive positional slots. A player holds a slot by
    having strictly higher ELO than any other claimant. If ELOs are tied, the
    current slot holder retains the position until their ELO is strictly exceeded.
    """
    from sqlalchemy import select
    from app.models import Rating

    result = await db.execute(
        select(Rating).where(Rating.format == format, Rating.player_id.isnot(None))
    )
    ratings = result.scalars().all()

    # Separate eligible (Champion+) and non-eligible
    eligible = [r for r in ratings if r.elo >= TOP_RANK_MIN_ELO]
    non_eligible = [r for r in ratings if r.elo < TOP_RANK_MIN_ELO]

    def sort_key(r):
        pos = r.top_position if r.top_position is not None else 999
        return (-r.elo, pos)

    eligible.sort(key=sort_key)

    # Clear top positions for non-eligible players
    for r in non_eligible:
        r.top_position = None

    # Assign top 1-10 among eligible players only
    for i, r in enumerate(eligible):
        r.top_position = i + 1 if i < 10 else None
