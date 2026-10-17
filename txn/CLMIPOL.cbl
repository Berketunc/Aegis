      *>****************************************************************
      *> CLMIPOL - decision rule for claims transaction CLMI.
      *>
      *> Reads one line on stdin (fixed columns, space separated):
      *>   cols  1-5   AI score        x10000  (00000-10000)
      *>   cols  7-11  tau_red         x10000
      *>   cols 13-17  tau_amber       x10000
      *>   col  19     duplicate flag      0/1
      *>   col  21     software-tag flag   0/1
      *>   col  23     risk-service-OK     0/1
      *>   col  25     low-resolution flag 0/1  (native short side
      *>               below the gateway's limit, 480 px by default)
      *> e.g. "09100 08500 06000 0 0 1 0"
      *>
      *> Writes one line on stdout:  BAND REASON[,REASON...]
      *> e.g. "RED AI_SCORE_ABOVE_RED"
      *>
      *> Every matching reason is reported; the band is the most
      *> severe one. RED never means rejection: it means referral to
      *> an investigator. A risk-service failure is AMBER, never GREEN.
      *> On a low-resolution photo the AI score alone can give AMBER
      *> but not RED (the detector's false-positive rate is higher on
      *> small photos); the duplicate-photo rule still gives RED.
      *>
      *> Demo: GnuCOBOL on LinuxONE (cobc -x CLMIPOL.cbl).
      *> Production: linked into the CICS transaction CLMI on z/OS.
      *>****************************************************************
       IDENTIFICATION DIVISION.
       PROGRAM-ID. CLMIPOL.

       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01  WS-INPUT-LINE            PIC X(80) VALUE SPACES.
       01  WS-IN REDEFINES WS-INPUT-LINE.
           05  IN-SCORE             PIC 9(5).
           05  FILLER               PIC X.
           05  IN-TAU-RED           PIC 9(5).
           05  FILLER               PIC X.
           05  IN-TAU-AMBER         PIC 9(5).
           05  FILLER               PIC X.
           05  IN-DUP               PIC 9.
           05  FILLER               PIC X.
           05  IN-SWTAG             PIC 9.
           05  FILLER               PIC X.
           05  IN-RISK-OK           PIC 9.
           05  FILLER               PIC X.
           05  IN-LOWRES            PIC 9.
           05  FILLER               PIC X(55).

       01  WS-BAND                  PIC X(5)   VALUE "GREEN".
       01  WS-SEVERITY              PIC 9      VALUE 0.
           88  SEV-GREEN                       VALUE 0.
           88  SEV-AMBER                       VALUE 1.
           88  SEV-RED                         VALUE 2.
       01  WS-REASONS               PIC X(120) VALUE SPACES.
       01  WS-PTR                   PIC 9(3)   VALUE 1.
       01  WS-COUNT                 PIC 9(2)   VALUE 0.
       01  WS-NEW-REASON            PIC X(30)  VALUE SPACES.
       01  WS-NEW-SEV               PIC 9      VALUE 0.
       01  WS-OUT                   PIC X(130) VALUE SPACES.

       PROCEDURE DIVISION.
       MAIN-PARA.
           ACCEPT WS-INPUT-LINE
           IF IN-SCORE IS NOT NUMERIC OR IN-TAU-RED IS NOT NUMERIC
              OR IN-TAU-AMBER IS NOT NUMERIC OR IN-DUP IS NOT NUMERIC
              OR IN-SWTAG IS NOT NUMERIC OR IN-RISK-OK IS NOT NUMERIC
              OR IN-LOWRES IS NOT NUMERIC
               DISPLAY "AMBER POLICY_INPUT_INVALID"
               STOP RUN
           END-IF

           IF IN-RISK-OK = 0
               MOVE "RISK_SERVICE_UNAVAILABLE" TO WS-NEW-REASON
               MOVE 1 TO WS-NEW-SEV
               PERFORM ADD-REASON
           ELSE
               IF IN-DUP = 1
                   MOVE "DUPLICATE_PHOTO" TO WS-NEW-REASON
                   MOVE 2 TO WS-NEW-SEV
                   PERFORM ADD-REASON
               END-IF
               IF IN-SCORE >= IN-TAU-RED AND IN-LOWRES = 1
                   MOVE "AI_SCORE_ABOVE_RED" TO WS-NEW-REASON
                   MOVE 1 TO WS-NEW-SEV
                   PERFORM ADD-REASON
                   MOVE "LOW_RESOLUTION_EVIDENCE" TO WS-NEW-REASON
                   MOVE 1 TO WS-NEW-SEV
                   PERFORM ADD-REASON
               ELSE
               IF IN-SCORE >= IN-TAU-RED
                   MOVE "AI_SCORE_ABOVE_RED" TO WS-NEW-REASON
                   MOVE 2 TO WS-NEW-SEV
                   PERFORM ADD-REASON
               ELSE
                   IF IN-SCORE >= IN-TAU-AMBER
                       MOVE "AI_SCORE_ABOVE_AMBER" TO WS-NEW-REASON
                       MOVE 1 TO WS-NEW-SEV
                       PERFORM ADD-REASON
                   END-IF
               END-IF
               END-IF
               IF IN-SWTAG = 1
                   MOVE "EDITING_SOFTWARE_TAG" TO WS-NEW-REASON
                   MOVE 1 TO WS-NEW-SEV
                   PERFORM ADD-REASON
               END-IF
           END-IF

           IF WS-COUNT = 0
               MOVE "NONE" TO WS-REASONS
           END-IF

           EVALUATE TRUE
               WHEN SEV-RED   MOVE "RED"   TO WS-BAND
               WHEN SEV-AMBER MOVE "AMBER" TO WS-BAND
               WHEN OTHER     MOVE "GREEN" TO WS-BAND
           END-EVALUATE

           STRING WS-BAND     DELIMITED BY SPACE
                  " "         DELIMITED BY SIZE
                  WS-REASONS  DELIMITED BY SPACE
                  INTO WS-OUT
           END-STRING
           DISPLAY FUNCTION TRIM(WS-OUT)
           STOP RUN.

       ADD-REASON.
           IF WS-COUNT > 0
               STRING "," DELIMITED BY SIZE
                      INTO WS-REASONS WITH POINTER WS-PTR
               END-STRING
           END-IF
           STRING WS-NEW-REASON DELIMITED BY SPACE
                  INTO WS-REASONS WITH POINTER WS-PTR
           END-STRING
           ADD 1 TO WS-COUNT
           IF WS-NEW-SEV > WS-SEVERITY
               MOVE WS-NEW-SEV TO WS-SEVERITY
           END-IF.
