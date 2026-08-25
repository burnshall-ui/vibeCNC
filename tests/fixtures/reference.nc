%
O0001 (VIBECNC PARSER REFERENCE)
(Exercises every branch the parser has: absolute and incremental words,)
(I/K arcs and R arcs, a roughing cycle, a threading cycle, a dwell, a speed)
(cap, a reference run and nose radius compensation. The expected counts and)
(path lengths are asserted in tests/test_reference.py -- keep the two in step.)
(Chuck limit for this program is Z-45. The last two cutting blocks)
(deliberately run past it and must be flagged as collisions.)

N010 G21 G18 G40 G80 G97 G99   (safe start block)
N015 G54                       (work offset)
N020 G50 S2500                 (speed cap, not a speed and not a move)
N030 T0101 M06                 (tool change 1)
N040 G96 S180 M03
N050 G00 X52. Z2.              (rapid 1)
N060 G01 Z0. F0.2              (cut 1)

(--- roughing cycle: the definition blocks draw nothing, and the contour)
(--- between P and Q is a shape rather than a run of moves. Ten layers)
(--- three millimetres apart, then one pass along the roughed contour.)
N070 G71 U1.5 R0.5
N080 G71 P090 Q110 U0.4 W0.1 F0.25
N090 X20. F0.15
N100 Z-10.
N110 X30. Z-15.

(--- arcs, outside the cycle, so they stay arcs ---)
N120 G00 X30. Z-15.            (rapid 2, onto the end of the contour)
N130 G02 X40. Z-20. R5.        (arc 1, R form)
N140 G03 X50. Z-25. I5. K0.    (arc 2, I/K form)
N150 G01 Z-40. F0.2            (cut 2)

(--- dwell: X is a time here, not a target ---)
N160 G04 X1.0

(--- incremental words ---)
N170 G00 U4. W5.               (rapid 3)
N180 G01 W-8.                  (cut 3)

(--- nose radius compensation, deliberately past the chuck limit ---)
N190 G42
N200 G01 X40. Z-60.            (cut 4, compensated, collision 1)
N210 G02 X30. Z-65. R5.        (arc 3, compensated, collision 2)
N220 G40

(--- reference run: no move, mode must survive ---)
N230 G28 U0 W0
N240 M30
%
