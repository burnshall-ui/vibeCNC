%
O0001 (VIBECNC PARSER REFERENCE)
(Exercises every branch the parser has: absolute and incremental words,)
(I/K arcs and R arcs, a roughing cycle, a dwell, a speed cap, a reference)
(run and nose radius compensation. The expected segment counts and path)
(lengths are asserted in tests/test_reference.py -- keep the two in step.)
(Chuck limit for this program is Z-45. The last two cutting blocks)
(deliberately run past it and must be flagged as collisions.)

N010 G21 G18 G40
N020 G50 S2500                 (speed cap, not a speed and not a move)
N030 T0101 M06                 (tool change 1)
N040 G96 S180 M03
N050 G00 X52. Z2.              (rapid 1)
N060 G01 Z0. F0.2              (cut 1)

(--- roughing cycle: the definition blocks must not draw anything ---)
N070 G71 U1.5 R0.5
N080 G71 P090 Q140 U0.4 W0.1 F0.25

(--- contour between P and Q ---)
N090 X20. F0.15                (cut 2, modal G01 -- a sticky G71 eats this)
N100 Z-10.                     (cut 3, modal G01, no G word)
N110 X30. Z-15.                (cut 4)
N120 G02 X40. Z-20. R5.        (arc 1, R form)
N130 G03 X50. Z-25. I5. K0.    (arc 2, I/K form)
N140 G01 Z-40.                 (cut 5)

(--- dwell: X is a time here, not a target ---)
N150 G04 X1.0

(--- incremental words ---)
N160 G00 U4. W5.               (rapid 2)
N170 G01 W-8.                  (cut 6)

(--- nose radius compensation, deliberately past the chuck limit ---)
N180 G42
N190 G01 X40. Z-60.            (cut 7, compensated, collision 1)
N200 G02 X30. Z-65. R5.        (arc 3, compensated, collision 2)
N210 G40

(--- reference run: no move, mode must survive ---)
N220 G28 U0 W0
N230 M30
%
