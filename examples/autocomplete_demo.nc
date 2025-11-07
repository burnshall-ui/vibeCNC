%
O1234 (AUTOCOMPLETE DEMO - VIBE CNC)
;
; === ANLEITUNG: Autocomplete testen ===
;
; 1. Tippe "G" und warte auf Vorschläge (G00, G01, G71, ...)
; 2. Tippe "G7" → siehst du G70-G76 Zyklen
; 3. Tippe "M" → siehst du M-Codes (M03, M08, M30, ...)
; 4. Tippe "T" → siehst du deine Tools aus der Library
; 5. Drücke Ctrl+Space für manuelle Aktivierung
;
; === PROGRAMM START ===

N10 ;
N20 G21 G18 G40 G80 G97 ;
N30 G50 S3000 ;

; Teste hier: Tippe "G" für G-Code-Vorschläge


; Teste hier: Tippe "T0" für Tool-Vorschläge


; Teste hier: Tippe "M" für M-Code-Vorschläge


; Teste hier: Tippe "M98 P" für Makro-Vorschläge


N999 M30 ;
%

