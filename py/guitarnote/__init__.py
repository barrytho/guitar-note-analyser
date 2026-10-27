"""Measure how much a guitar pedal clips, one recorded note at a time.

The measure is the crest factor: the ratio of a note's peak to its RMS level.
Clipping flattens the peaks while the RMS barely moves, so the crest factor
falls as the gain knob is turned up. Everything here uses the Python standard
library only, so the tool runs on any Python 3.9 or later with nothing to
install.
"""

__version__ = "0.2.0"
