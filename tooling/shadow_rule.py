#!/usr/bin/env python3
"""The bar a shadow arm must clear before it can change the page.

Fixed before the sample is complete, so the decision cannot be made after seeing
the result. One pick per race is a coarse measurement: with the two arms
disagreeing on about 30% of races, 2,000 races put a 95% interval of roughly
+-2.4 points on the strike-rate lead, so a three-point lead is detectable at
about 80% power. A 1.5-point gain would need about 8,300 races to see.

The rule, applied to the races where the two arms disagree (McNemar's pairs):

  * decision point   2,000 settled races, no early adoption
  * effect floor     strike-rate lead of at least 3.0 points
  * significance     exact one-sided McNemar p below 0.05
  * otherwise        the arm is retired at the decision point, not kept open

usage: python3 tooling/shadow_rule.py [picks_ab|picks_market_ab]
"""
import math
import sys

DECISION_RACES = 2000
MIN_LEAD_POINTS = 3.0
ALPHA = 0.05


def mcnemar(b, c):
    """One-sided exact McNemar p for the challenger doing better than the page."""
    n = b + c
    if not n:
        return 1.0
    if n > DECISION_RACES:      # normal approximation, keeps the sum cheap
        z = (b - c) / math.sqrt(n)
        return 0.5 * math.erfc(z / math.sqrt(2))
    tail = sum(math.comb(n, k) for k in range(b, n + 1))
    return tail / 2 ** n


def decide(races, b, c):
    """Apply the pre-registered rule to the discordant pair counts."""
    lead = 100.0 * (b - c) / races if races else 0.0
    p = mcnemar(b, c)
    if races < DECISION_RACES:
        status = "not yet"
    elif lead >= MIN_LEAD_POINTS and p < ALPHA:
        status = "adopt"
    else:
        status = "retire"
    return {"status": status, "races": races, "b": b, "c": c, "lead": lead,
            "p": p, "need": max(0, DECISION_RACES - races)}


def sentence(d, subject):
    """The rule's verdict as one plain sentence for the page."""
    if d["status"] == "adopt":
        return (f"The {subject} cleared the pre-registered bar: "
                f"{d['lead']:+.1f} points over {d['races']} settled races, "
                f"one-sided p {d['p']:.3f}. Adoption is earned, and is still a "
                f"separate reviewed edit.")
    if d["status"] == "retire":
        return (f"The {subject} missed the pre-registered bar at {d['races']} "
                f"settled races ({d['lead']:+.1f} points, one-sided p "
                f"{d['p']:.2f}) and is retired; its record stays.")
    return (f"The bar was fixed in advance: {DECISION_RACES} settled races, a "
            f"lead of at least {MIN_LEAD_POINTS:.1f} points, and a one-sided "
            f"McNemar p below 0.05. With {d['races']} races and a lead of "
            f"{d['lead']:+.1f} points (p {d['p']:.2f}), {d['need']} more are "
            f"needed. Nothing changes before then.")


def main(argv):
    sys.path.insert(0, __file__.rsplit("/", 1)[0])
    import ab_tally
    import market_arm_day
    snap_dir = "picks_market_ab"
    subject = market_arm_day.SUBJECT
    if len(argv) > 1 and argv[1] == "picks_ab":
        snap_dir, subject = "picks_ab", "rating"
    t = ab_tally.collect(snap_dir=snap_dir, subject=subject)
    if not t["races"]:
        print("no settled shadow races yet")
        return 1
    d = decide(t["races"], t["changed_won"], t["changed_lost"])
    print(f"{subject}: {t['races']} races, the arm won {t['changed_won']} of "
          f"the races where the picks differed and the page won "
          f"{t['changed_lost']}")
    print(f"  lead {d['lead']:+.1f} points, one-sided p {d['p']:.3f}, "
          f"{d['need']} races to the decision point")
    print(sentence(d, subject))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
