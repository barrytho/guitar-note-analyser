"""Crest factor of one recorded note, with every step of the working kept.

The recipe, fixed so that every note is measured the same way:

1. Find the pluck: split the take into 5 ms frames and take the first frame
   whose RMS reaches 10% (-20 dB) of the loudest frame's.
2. Skip the first 50 ms after it, which is the pick attack rather than the
   note, and analyse the next 300 ms.
3. Over that window: peak = max |x|, RMS = sqrt(mean of x^2), and the crest
   factor is 20 log10(peak / RMS) in dB.

The onset is the first frame to cross a threshold, not the loudest frame,
because a heavily clipped note is so compressed that its loudest frame can sit
anywhere in the sustain.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

FRAME_S = 0.005             #: envelope frame for finding the pluck
ONSET_FRACTION = 0.10       #: of the loudest frame's RMS, i.e. -20 dB
WINDOW_DELAY_S = 0.050      #: skip the pick attack
WINDOW_LENGTH_S = 0.300     #: the part of the note that is analysed
SILENCE_DBFS = -50.0        #: a take quieter than this holds no note
CLIP_WARNING_DBFS = -0.1    #: a take this loud may have clipped the interface
ZOOM_S = 0.030              #: span of the close-up chart around the peak
OVERVIEW_BINS = 600         #: columns in the whole-take chart

METHOD = (f"crest factor over {WINDOW_LENGTH_S * 1000:.0f} ms starting "
          f"{WINDOW_DELAY_S * 1000:.0f} ms after the onset")


class NoteError(ValueError):
    """The recording cannot be measured; the message says why and what to do."""


@dataclass
class Measurement:
    sample_rate: int
    n_samples: int
    take_peak_dbfs: float
    onset_s: float
    window_start_s: float
    window_end_s: float
    window_samples: int
    peak: float
    peak_s: float
    sum_squares: float
    mean_square: float
    rms: float
    crest_ratio: float
    crest_db: float
    warnings: list[str]

    def as_dict(self) -> dict:
        return asdict(self)


def dbfs(value: float) -> float:
    return 20.0 * math.log10(value) if value > 0 else float("-inf")


def num(value: float, digits: int = 4) -> str:
    """A number to ``digits`` significant figures, without scientific notation
    for the magnitudes a waveform produces."""
    if value == 0 or not math.isfinite(value):
        return str(value)
    decimals = max(0, digits - 1 - int(math.floor(math.log10(abs(value)))))
    return f"{value:,.{decimals}f}"


def _step(title: str, formula: str, working: str, result: str) -> dict:
    return {"title": title, "formula": formula, "working": working, "result": result}


def find_onset(samples: list[float], sample_rate: int) -> tuple[int, float]:
    """Return ``(onset_sample, loudest_frame_rms)``."""
    frame = max(1, int(round(FRAME_S * sample_rate)))
    rms = [math.sqrt(sum(v * v for v in samples[i:i + frame]) / frame)
           for i in range(0, len(samples) - frame + 1, frame)]
    if not rms:
        raise NoteError("The recording is too short to contain a note.")
    loudest = max(rms)
    threshold = ONSET_FRACTION * loudest
    first = next(i for i, value in enumerate(rms) if value >= threshold)
    return first * frame, loudest


def measure(samples: list[float], sample_rate: int) -> tuple[Measurement, list[dict]]:
    """Measure one note; return the numbers and the working that produced them."""
    n = len(samples)
    if n == 0:
        raise NoteError("The recording is empty.")
    take_peak = max(abs(v) for v in samples)
    take_peak_dbfs = dbfs(take_peak)
    if take_peak_dbfs < SILENCE_DBFS:
        raise NoteError(f"The recording peaks at {take_peak_dbfs:.1f} dBFS, too quiet to hold "
                        "a note. Check the input is connected and the level is up.")
    warnings = []
    if take_peak_dbfs >= CLIP_WARNING_DBFS:
        warnings.append(f"The take reaches {take_peak_dbfs:.2f} dBFS, so the recording interface "
                        "may have clipped. That squashes peaks exactly as a pedal does and would "
                        "read as extra distortion. Lower the input gain and record again.")

    onset, loudest = find_onset(samples, sample_rate)
    start = onset + int(round(WINDOW_DELAY_S * sample_rate))
    length = int(round(WINDOW_LENGTH_S * sample_rate))
    end = start + length
    if end > n:
        needed = end / sample_rate
        raise NoteError(f"The note needs to ring until {needed:.2f} s into the take but the "
                        f"recording stops at {n / sample_rate:.2f} s. Let the note ring longer "
                        "before stopping.")
    window = samples[start:end]

    peak_index = max(range(length), key=lambda i: abs(window[i]))
    peak = abs(window[peak_index])
    sum_squares = math.fsum(v * v for v in window)
    mean_square = sum_squares / length
    rms = math.sqrt(mean_square)
    ratio = peak / rms
    crest = 20.0 * math.log10(ratio)

    m = Measurement(
        sample_rate=sample_rate, n_samples=n, take_peak_dbfs=take_peak_dbfs,
        onset_s=onset / sample_rate, window_start_s=start / sample_rate,
        window_end_s=end / sample_rate, window_samples=length,
        peak=peak, peak_s=(start + peak_index) / sample_rate,
        sum_squares=sum_squares, mean_square=mean_square, rms=rms,
        crest_ratio=ratio, crest_db=crest, warnings=warnings)

    frame_ms = FRAME_S * 1000
    steps = [
        _step("Read the recording",
              "duration = samples / sample rate",
              f"{n:,} / {sample_rate:,} Hz",
              f"{n / sample_rate:.3f} s; loudest sample {take_peak_dbfs:.1f} dBFS"),
        _step("Find the pluck",
              f"RMS of each {frame_ms:.0f} ms frame; onset = first frame reaching "
              f"{ONSET_FRACTION:.0%} of the loudest",
              f"loudest frame RMS {num(loudest)}; threshold {num(ONSET_FRACTION * loudest)}",
              f"onset at {m.onset_s:.3f} s"),
        _step("Choose the analysis window",
              f"start = onset + {WINDOW_DELAY_S * 1000:.0f} ms; "
              f"length = {WINDOW_LENGTH_S * 1000:.0f} ms",
              f"{m.onset_s:.3f} + {WINDOW_DELAY_S:.3f} s, for {length:,} samples",
              f"{m.window_start_s:.3f} s to {m.window_end_s:.3f} s"),
        _step("Peak",
              "peak = max |x|",
              f"largest magnitude, at {m.peak_s:.4f} s (sample {start + peak_index:,})",
              f"peak = {num(peak)}"),
        _step("Mean of the squares",
              "mean square = (x₁² + x₂² + … + xₙ²) / N",
              f"{num(sum_squares)} / {length:,}",
              f"mean square = {num(mean_square)}"),
        _step("RMS",
              "RMS = √(mean square)",
              f"√{num(mean_square)}",
              f"RMS = {num(rms)}"),
        _step("Peak-to-RMS ratio",
              "ratio = peak / RMS",
              f"{num(peak)} / {num(rms)}",
              f"ratio = {ratio:.4f}"),
        _step("Crest factor",
              "crest factor = 20 × log₁₀(ratio)",
              f"20 × log₁₀({ratio:.4f})",
              f"{crest:.2f} dB"),
    ]
    return m, steps


def chart_data(samples: list[float], m: Measurement) -> dict:
    """What the page needs to draw a note: a min/max overview of the whole take
    and a full-resolution close-up of the window around its peak."""
    sr = m.sample_rate
    n = len(samples)
    bins = min(OVERVIEW_BINS, n)
    overview = []
    for b in range(bins):
        chunk = samples[b * n // bins:(b + 1) * n // bins] or [0.0]
        overview.append([round((b + 0.5) * n / bins / sr, 5),
                         round(min(chunk), 6), round(max(chunk), 6)])

    start = int(round(m.window_start_s * sr))
    end = start + m.window_samples
    span = int(round(ZOOM_S * sr))
    peak_at = int(round(m.peak_s * sr))
    lo = max(start, min(peak_at - span // 3, end - span))
    hi = min(end, lo + span)
    zoom = [[round((i - lo) / sr * 1000, 4), round(samples[i], 6)] for i in range(lo, hi)]
    return {"overview": overview, "zoom": zoom,
            "zoom_start_s": lo / sr, "peak_ms": (peak_at - lo) / sr * 1000}
