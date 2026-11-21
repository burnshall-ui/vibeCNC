# readout.py — what the position display shows for a block, free of GUI
# dependencies.
#
# The display beside the simulation reads like the control's position screen:
# where the tool is, which tool, the spindle speed and the feed, and the modal
# G- and M-codes in force. The modal state comes from the parser
# (paths['modal']), the position from the simulation's tool point, so the
# display and the picture can never tell two stories.
#
# Speeds and feeds are worked out the way the control commands them, not
# measured: under G96 the spindle turns at 1000 S / (pi D) at the tool's
# diameter, capped by G50 S; under G99 the feed per minute is F times that.
# A rapid's speed is a machine parameter that is not known here, so it says
# "rapid" rather than a number.
#
# The parser's state is per block, and a cycle is one block that makes many
# moves: after a G71 the modal motion is still the G00 before it, while its
# passes cut at feed. So the display is told what the move being made is,
# and the motion code and the feed follow that move.
import math

# The order the modal codes are shown in: motion, then the settings a header
# makes, then the spindle.
CODE_ORDER = ('motion', 'plane', 'units', 'comp', 'feed_mode', 'css', 'wcs', 'spindle')
RAPID_CODES = ('G00',)


def spindle_rpm(modal, x):
    """Revolutions per minute at diameter x, or None when the spindle is not
    turning or its speed is not known."""
    if not modal or modal.get('spindle') == 'M05':
        return None
    s = modal.get('s') or 0
    cap = modal.get('s_max') or 0
    if modal.get('css') == 'G96':
        if s <= 0:
            return None
        diameter = abs(x) if x is not None else 0.0
        if diameter <= 1e-9:
            # At the centre line constant surface speed asks for infinity;
            # the control runs at its G50 limit.
            return float(cap) if cap > 0 else None
        rpm = 1000.0 * s / (math.pi * diameter)
        return min(rpm, float(cap)) if cap > 0 else rpm
    return float(s) if s > 0 else None


def feed_per_minute(modal, rpm):
    """mm (or inch) per minute, or None when it cannot be known."""
    if not modal:
        return None
    f = modal.get('f') or 0.0
    if f <= 0:
        return None
    if modal.get('feed_mode') == 'G98':
        return f
    return f * rpm if rpm else None


def active_codes(modal) -> list:
    """The modal codes in force, in display order. Unset groups are left out."""
    if not modal:
        return []
    return [modal[group] for group in CODE_ORDER if modal.get(group)]


def _thousands(value) -> str:
    """1989 as '1 989': a narrow gap, the way the screen groups digits."""
    return f"{value:,.0f}".replace(",", " ")


# The kind of move the simulation is making, as the motion code group 01 shows it.
MOVE_CODES = {'rapid': 'G00', 'cut': 'G01', 'finish': 'G01', 'thread': 'G32'}


def readout(modal, x, z, tool=None, move=None) -> dict:
    """The display's text for one moment of the playback.

    modal: the parser's state after the block; x (diameter) and z: where the
    tool is; tool: its record from tools.json, or None; move: the kind of the
    move being made ('rapid', 'cut', 'finish', 'thread', 'arc'), or None.
    Every value is a string, '' where there is nothing to show.
    """
    modal = dict(modal or {})
    if move in MOVE_CODES:
        modal['motion'] = MOVE_CODES[move]
    unit = 'in' if modal.get('units') == 'G20' else 'mm'
    rpm = spindle_rpm(modal, x)
    number = modal.get('tool') or 0

    if modal.get('css') == 'G96' and modal.get('s'):
        speed = f"{modal['s']} {'ft' if unit == 'in' else 'm'}/min"
    elif modal.get('s'):
        speed = f"{modal['s']} /min"
    else:
        speed = ''
    if modal.get('spindle') == 'M05' or not modal.get('spindle'):
        speed_actual = 'stopped' if modal.get('spindle') == 'M05' else ''
    else:
        speed_actual = f"SACT {_thousands(rpm)} /min" if rpm else ''

    f = modal.get('f') or 0.0
    if modal.get('motion') in RAPID_CODES:
        feed, feed_actual = 'rapid', ''
    elif f > 0:
        per = f"{unit}/min" if modal.get('feed_mode') == 'G98' else f"{unit}/rev"
        feed = f"{f:g} {per}"
        minute = feed_per_minute(modal, rpm)
        feed_actual = (f"ACT.F {minute:.0f} {unit}/min"
                       if minute is not None and modal.get('feed_mode') != 'G98' else '')
    else:
        feed, feed_actual = '', ''

    return {
        'x': '' if x is None else f"{x:.3f}".replace('-', '−'),
        'z': '' if z is None else f"{z:.3f}".replace('-', '−'),
        'tool': f"T{number:02d}" if number else '',
        'tool_name': str((tool or {}).get('name') or '') if number else '',
        'speed': speed,
        'speed_actual': speed_actual,
        'feed': feed,
        'feed_actual': feed_actual,
        'codes': active_codes(modal),
    }
