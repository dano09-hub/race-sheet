#!/usr/bin/env python3
"""Standard UK each-way terms and each-way EV maths.

Pure standard-library functions so both the back-test and the fixture suite can
import them without pandas/numpy.

The terms below are the standard place terms for GB/IRE racing. A bookmaker can
offer enhanced terms (extra places or a bigger fraction); pass those explicitly
when they differ.
"""


def place_terms(field_size, handicap):
    """Return (places, place_fraction) for standard each-way terms.

    Win-only races return (0, 0.0). Fractions are the place odds fraction:
    1/4 is 0.25, 1/5 is 0.2.
    """
    n = int(field_size)
    if n < 5:
        return (0, 0.0)
    if n <= 7:
        return (2, 0.25)
    if handicap:
        if n <= 11:
            return (3, 0.20)
        if n <= 15:
            return (3, 0.25)
        return (4, 0.25)
    return (3, 0.20)


def place_decimal(win_decimal, fraction):
    """Decimal odds for the place part: 1 + fraction * (win decimal - 1)."""
    if win_decimal is None or win_decimal <= 1.0:
        raise ValueError("win_decimal must be > 1")
    if fraction <= 0:
        raise ValueError("fraction must be > 0")
    return 1.0 + fraction * (win_decimal - 1.0)


def each_way_ev(p_win, p_place, win_decimal, fraction):
    """EV of a 1-unit win + 1-unit place bet, in units.

    p_place must be the inclusive place probability (win or finish in the paid
    places). Returns None for a win-only race.
    """
    if fraction <= 0:
        return None
    for name, p in (("p_win", p_win), ("p_place", p_place)):
        if not 0.0 <= p <= 1.0:
            raise ValueError("%s must be between 0 and 1" % name)
    d_place = place_decimal(win_decimal, fraction)
    return p_win * win_decimal + p_place * d_place - 2.0


def place_only_ev(p_place, win_decimal, fraction):
    """EV of a 1-unit place-only bet, in units."""
    if fraction <= 0:
        return None
    if not 0.0 <= p_place <= 1.0:
        raise ValueError("p_place must be between 0 and 1")
    return p_place * place_decimal(win_decimal, fraction) - 1.0


def describe_terms(field_size, handicap):
    places, fraction = place_terms(field_size, handicap)
    if not places:
        return "Win only at standard terms"
    return "%d places \u00b7 %d/%d odds" % (places, 1, int(round(1.0 / fraction)))


def _decimal(odds):
    s = str(odds).strip().lower()
    if s in ("evs", "evens"):
        return 2.0
    if "/" in s:
        a, b = s.split("/", 1)
        return 1.0 + float(a) / float(b)
    v = float(s)
    if v <= 1.0:
        raise ValueError("odds must be fractional (e.g. 8/1) or decimal > 1")
    return v


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Standard each-way terms and EV calculator")
    ap.add_argument("--odds", required=True, help="win odds, e.g. 8/1 or 9.0")
    ap.add_argument("--p-win", type=float, required=True, help="win probability, 0-1")
    ap.add_argument("--p-place", type=float, required=True, help="inclusive place probability, 0-1")
    ap.add_argument("--field", type=int, required=True, help="number of actual runners")
    ap.add_argument("--handicap", action="store_true", help="handicap race")
    args = ap.parse_args()
    places, fraction = place_terms(args.field, args.handicap)
    dec = _decimal(args.odds)
    print("Terms: " + describe_terms(args.field, args.handicap))
    if not places:
        print("No each-way place part: win-only race.")
    else:
        dp = place_decimal(dec, fraction)
        print("Win decimal: %.3f | place decimal: %.3f" % (dec, dp))
        print("Each-way EV: %+.3f units per 1 win + 1 place" % each_way_ev(
            args.p_win, args.p_place, dec, fraction))
        print("Place-only EV: %+.3f units per 1 unit" % place_only_ev(
            args.p_place, dec, fraction))
