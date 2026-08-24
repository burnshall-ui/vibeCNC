# arc_geometry.py — arc angle maths for the plotter, free of GUI dependencies.
#
# Kept out of gcode_plotter so the direction logic can be tested on a bare
# interpreter, like the parser and the lint engine. Whether an operator sees the
# programmed arc or its complement is not a cosmetic question, so it belongs in
# the fast CI job rather than behind a Qt import.
import math


def arc_thetas(arc: dict) -> tuple:
    """Returns (theta1, theta2) for matplotlib from one parsed G02/G03 arc.

    matplotlib's Arc always sweeps counter-clockwise from theta1 to theta2, and
    normalises both angles modulo 360 before drawing. A clockwise arc is
    therefore drawn by handing over the two endpoints the other way round --
    the same set of points, traversed the other way, which for a static patch
    looks identical.

    Subtracting 360 from theta2 does not work: the normalisation undoes it and
    the complementary arc comes out. Note the asymmetry -- the same subtraction
    applied to theta1 does survive, which is why only G02 was ever wrong.

    Angles are computed in radius space because X values are diameters.
    """
    cx, cz = arc['center']
    x1, z1 = arc['start']
    x2, z2 = arc['end']

    cr = cx / 2.0
    angle1 = math.degrees(math.atan2(z1 - cz, x1 / 2.0 - cr))
    angle2 = math.degrees(math.atan2(z2 - cz, x2 / 2.0 - cr))

    if abs(x1 - x2) < 1e-9 and abs(z1 - z2) < 1e-9:
        # Full circle: I/K with no X/Z, so the endpoints and therefore the
        # angles coincide. Leaving it at angle1 twice makes matplotlib draw
        # nothing at all, so hand back a whole turn. Direction does not matter
        # here -- both ways cover the same points.
        return angle1, angle1 + 360.0

    if arc['cw']:      # G02
        return angle2, angle1
    return angle1, angle2   # G03


def arc_sweep(arc: dict) -> float:
    """Degrees the arc covers, following matplotlib's own normalisation.

    Path.arc shifts theta2 into the turn above theta1 and, when the two are
    written differently but land on the same angle, adds a further full turn.
    A plain (theta2 - theta1) % 360 gets the full circle wrong -- it yields 0.
    """
    theta1, theta2 = arc_thetas(arc)
    eta2 = theta2 - 360.0 * math.floor((theta2 - theta1) / 360.0)
    if theta2 != theta1 and eta2 <= theta1:
        eta2 += 360.0
    return eta2 - theta1
