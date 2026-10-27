# Guitar note analyser

Measures how much a guitar pedal clips, one recorded note at a time, and tests
whether that follows the gain knob. Open it at **https://barrytho.github.io/guitar-note-analyser/**

Each note's crest factor (peak ÷ RMS, in dB) is measured, with every step of
the calculation shown. Clipping flattens the peaks, so more gain should mean a
lower crest factor. The page records from your audio interface, or takes WAV
uploads, and exports its charts and data.

The Python runs inside your browser via [Pyodide](https://pyodide.org). Your
notes and recordings stay in your browser and are never uploaded.

This repository holds only the published site and is generated automatically:
anything changed here directly is overwritten by the next publish.
