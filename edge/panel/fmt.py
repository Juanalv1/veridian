"""Small Spanish text helpers shared by the panel modules."""


def count(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def seconds_text(secs: float) -> str:
    """Seconds as short Spanish text: "2 s", "2,5 s", "45 s", "2 min 5 s"."""
    if secs < 10 and round(secs, 1) < 10:
        text = f"{secs:.1f}".replace(".", ",")
        return text.removesuffix(",0") + " s"
    whole = round(secs)
    if whole < 60:
        return f"{whole} s"
    minutes, rest = divmod(whole, 60)
    return f"{minutes} min {rest} s" if rest else f"{minutes} min"
