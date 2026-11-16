# gcode_text.py — text rules for G-code as it is typed, free of GUI dependencies.
#
# Kept apart from the editor so they can be tested without PyQt6 present.


def uppercase_code(line: str) -> str:
    """Upper-cases one block of G-code and leaves its comments alone.

    Fanuc code is capitals, but a comment is prose: `(Material: C45, 40 mm)`
    must stay readable, and `mm` is not `MM`. A comment is a parenthesised span
    -- possibly unclosed, because it is being typed -- or everything after a
    semicolon, which is how the parser reads it too.

    The result is always exactly as long as the input and differs only in
    letter case, so a caller can apply it character by character without
    moving a cursor.
    """
    out = []
    in_parentheses = False
    for index, char in enumerate(line):
        if in_parentheses:
            out.append(char)
            if char == ')':
                in_parentheses = False
        elif char == '(':
            in_parentheses = True
            out.append(char)
        elif char == ';':
            out.append(line[index:])
            break
        else:
            upper = char.upper()
            # 'ß'.upper() is 'SS': keep the original rather than change the length.
            out.append(upper if len(upper) == 1 else char)
    return "".join(out)
