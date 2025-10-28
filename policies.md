# Vibe CNC — Policies (Fanuc-Style)

**Zweck:** Diese Regeln werden von der KI und dem Linter erzwungen. Ziel ist robuste, nachvollziehbare NC-Programmierung.

## Header-Guards
- `G18 G40 G80 G97` müssen im Header gesetzt sein.
- Einheiten: `G21` (mm).
- Nullpunkt: `G54` (oder explizit im Programm dokumentieren).

## CSS & Drehzahlbegrenzung
- `G50` MUSS vor dem ersten `G96` stehen (Drehzahlbegrenzung vor Schnittgeschwindigkeit).

## Prozess-Invarianz
- Roboter-/Luft-M-Codes sind **invariant** und dürfen nicht geändert/entfernt werden
  (Liste ist in `config.yaml` definiert).

## Zyklen & Parameter
- G7x-Zyklen: keine negativen/Null-Zustellungen/Vorschübe; Wertebereiche plausibel halten.
- G76 Gewinde: sinnvolle Durchgänge und Endtiefe; Warnung bei extremen Abweichungen.
- `G4` Verweilzeiten: ≤ 5 s ohne Kommentar, sonst dokumentieren.

## Sicherer Ablauf
- Vor `M30`: erst `Z` in sichere Ebene, dann `X` rausfahren.
- Keine Änderung von Kühlluft/Signalen am Ende, wenn als „invariant“ markiert.

## Kommentare & Doku
- Kommentar-Header mit: Werkzeugtabelle, Material, Datum, Version (`REV:`).
- KI darf nur **Fix-Vorschläge** machen oder einen diff ausgeben; Änderungen werden manuell übernommen.
