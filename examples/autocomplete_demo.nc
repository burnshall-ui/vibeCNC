%
O1234 (AUTOCOMPLETE DEMO - VIBE CNC)
;
; === INSTRUCTIONS: Testing autocomplete ===
;
; 1. Type "G" and wait for suggestions (G00, G01, G71, ...)
; 2. Type "G7" → you see the G70-G76 cycles
; 3. Type "M" → you see M-codes (M03, M08, M30, ...)
; 4. Type "T" → you see your tools from the library
; 5. Press Ctrl+Space for manual activation
;
; === PROGRAM START ===

N10 ;
N20 G21 G18 G40 G80 G97 ;
N30 G50 S3000 ;

; Test here: type "G" for G-code suggestions


; Test here: type "T0" for tool suggestions


; Test here: type "M" for M-code suggestions


; Test here: type "M98 P" for macro suggestions


N999 M30 ;
%

