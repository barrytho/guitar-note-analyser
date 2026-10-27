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


def _step(title: str, formula: str, working: str, result: str, explain: str) -> dict:
    """One line of working. ``explain`` says in plain words where the numbers
    come from, what is done to them and what the result means."""
    return {"title": title, "formula": formula, "working": working, "result": result,
            "explain": explain}


#: Reference crest factors that give a measured value its meaning.
SINE_DB = 20.0 * math.log10(math.sqrt(2.0))      # 3.01 dB
REFERENCES = [
    {"db": 0.0, "label": "square wave (completely clipped)"},
    {"db": SINE_DB, "label": "pure sine wave"},
    {"db": 8.0, "label": "typical clean plucked note"},
]

#: Rough bands for a plucked guitar note, read off the simulated clipper. Not
#: for test tones: a pure sine is 3.01 dB without any clipping at all. A guide
#: only; what counts is the change from the same note at the lowest gain.
BANDS = [
    (6.0, "spiky",
     "For a plucked note, the peaks stand well above its typical level, as they do in "
     "a clean or lightly driven note: little or no clipping."),
    (3.5, "trimmed",
     "For a plucked note, the peaks are noticeably lower than a clean one's: the pedal "
     "is clipping the tops of the waveform."),
    (1.5, "flattened",
     "For a plucked note, most of the waveform is pressed against the clipping level: "
     "heavy clipping."),
    (float("-inf"), "nearly square",
     "Almost every sample sits at the peak, as in a square wave: about as clipped as a "
     "note gets."),
]


def describe_crest(crest_db: float) -> dict:
    """What a crest factor means, in words, with the references to read it by."""
    for floor, label, text in BANDS:
        if crest_db >= floor:
            return {"label": label, "text": text, "references": REFERENCES}
    raise AssertionError("the last band has no floor")


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
    frame = max(1, int(round(FRAME_S * sample_rate)))
    signed_peak = window[peak_index]
    reading = describe_crest(crest)
    steps = [
        _step("Read the recording",
              "duration = samples / sample rate",
              f"{n:,} / {sample_rate:,} Hz",
              f"{n / sample_rate:.3f} s; loudest sample {take_peak_dbfs:.1f} dBFS",
              f"A recording is a long list of numbers called samples, each the signal's "
              f"voltage at one instant, scaled so that ±1 is the loudest the file can "
              f"hold. This one has {sample_rate:,} samples per second, so {n:,} samples "
              f"make {n / sample_rate:.3f} seconds. The loudest of them is "
              f"{take_peak_dbfs:.1f} dBFS (decibels below full scale): 0 dBFS is the "
              f"ceiling, where the recording interface itself would start to clip and "
              f"spoil the measurement."),
        _step("Find the pluck",
              f"RMS of each {frame_ms:.0f} ms frame; onset = first frame reaching "
              f"{ONSET_FRACTION:.0%} of the loudest",
              f"loudest frame RMS {num(loudest)}; threshold {num(ONSET_FRACTION * loudest)}",
              f"onset at {m.onset_s:.3f} s",
              f"Every note is measured over the same stretch of its life, so first the "
              f"pluck has to be found. The take is cut into {n // frame:,} slices of "
              f"{frame_ms:.0f} ms and the typical level (RMS, step 6) of each is worked out. "
              f"The loudest slice reached {num(loudest)}, and the pluck is the first slice "
              f"to reach {ONSET_FRACTION:.0%} of that ({num(ONSET_FRACTION * loudest)}): "
              f"well above background noise, but early enough to catch the attack. The "
              f"loudest slice itself is not used, because a heavily clipped note is squashed "
              f"so flat that its loudest slice can fall anywhere in the note. The dashed "
              f"red line in the first chart marks this point."),
        _step("Choose the analysis window",
              f"start = onset + {WINDOW_DELAY_S * 1000:.0f} ms; "
              f"length = {WINDOW_LENGTH_S * 1000:.0f} ms",
              f"{m.onset_s:.3f} + {WINDOW_DELAY_S:.3f} s, for {length:,} samples",
              f"{m.window_start_s:.3f} s to {m.window_end_s:.3f} s",
              f"The first {WINDOW_DELAY_S * 1000:.0f} ms after the pluck is mostly the pick "
              f"striking the string, a click rather than the note, so it is skipped. The "
              f"next {WINDOW_LENGTH_S * 1000:.0f} ms ({length:,} samples) is what gets "
              f"measured: long enough to hold dozens of vibrations of even the lowest "
              f"guitar note, short enough that the note has barely started to fade. It is "
              f"the shaded band in the first chart. Every later step uses only these "
              f"samples."),
        _step("Peak",
              "peak = max |x|",
              f"largest magnitude, at {m.peak_s:.4f} s (sample {start + peak_index:,})",
              f"peak = {num(peak)}",
              f"The peak is the single biggest swing away from zero anywhere in the "
              f"window, upwards or downwards: the sign is ignored, which is what the bars "
              f"in |x| mean. Here it is the sample at {m.peak_s:.4f} s, whose value is "
              f"{num(signed_peak)}. Clipping works by capping exactly this: a pedal that "
              f"clips flattens the tops of the waveform so the peak cannot grow. It is the "
              f"dashed red line in the close-up chart."),
        _step("Mean of the squares",
              "mean square = (x₁² + x₂² + … + xₙ²) / N",
              f"{num(sum_squares)} / {length:,}",
              f"mean square = {num(mean_square)}",
              f"To find the note's typical level, every one of the {length:,} samples is "
              f"squared. Squaring makes them all positive, so swings above and below zero "
              f"do not cancel out, and it gives big swings more weight than small ones. "
              f"Added up, the squares come to {num(sum_squares)}; dividing by the "
              f"{length:,} samples gives their average, {num(mean_square)}, which is the "
              f"note's average power."),
        _step("RMS",
              "RMS = √(mean square)",
              f"√{num(mean_square)}",
              f"RMS = {num(rms)}",
              f"Taking the square root undoes the squaring, bringing the average back to "
              f"the same scale as the samples. The result, the root mean square, is the "
              f"note's effective level: roughly how loud it sounds. It is the solid green "
              f"line in the close-up chart."),
        _step("Peak-to-RMS ratio",
              "ratio = peak / RMS",
              f"{num(peak)} / {num(rms)}",
              f"ratio = {ratio:.4f}",
              f"This says how many times taller the highest peak is than the typical "
              f"level: here {ratio:.2f} times. In a clean plucked note the harmonics line "
              f"up into sharp peaks, giving a ratio of about 2.5. Clipping cuts the peaks "
              f"down much more than it lowers the RMS, so the ratio falls towards 1, which "
              f"is a square wave, where every sample sits at the peak. Turning a pedal's "
              f"level knob scales peak and RMS together, so it leaves the ratio alone. The "
              f"gap between the red and green lines in the close-up chart is this ratio."),
        _step("Crest factor",
              "crest factor = 20 × log₁₀(ratio)",
              f"20 × log₁₀({ratio:.4f})",
              f"{crest:.2f} dB",
              f"Audio levels are quoted in decibels, a scale on which equal steps are equal "
              f"proportions; for a ratio of amplitudes, decibels are 20 × log₁₀. "
              f"For reference, a completely clipped square wave is 0 dB, a pure sine wave "
              f"{SINE_DB:.2f} dB and a clean plucked note about 8 dB. At {crest:.2f} dB "
              f"this note reads as {reading['label']}. {reading['text']} Lower means more clipping, and the fairest reading is "
              f"against the same note at the pedal's lowest gain setting."),
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
