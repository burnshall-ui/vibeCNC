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

    if arc['cw']:      # G02
        return angle2, angle1
    return angle1, angle2   # G03
