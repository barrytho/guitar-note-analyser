"""The page's two background explanations, built from real computations.

* ``crest_picture()``: what crest factor is, shown with an actual clean pluck
  and the same pluck heavily clipped, each measured by ``measure()``.
* ``demo_recipe()``: how the demo notes are generated, by Fourier synthesis,
  quoting the parameters ``demo.py`` actually uses.

Both texts are written here, next to the numbers they quote, so the page only
lays them out (the same rule as the calculation steps).
"""
from __future__ import annotations

import math
import random

from . import demo
from .measure import chart_data, measure


def _measured(samples: list[float]) -> dict:
    m, _ = measure(samples, demo.SAMPLE_RATE)
    chart = chart_data(samples, m)
    return {"zoom": chart["zoom"], "peak": m.peak, "rms": m.rms,
            "ratio": m.crest_ratio, "crest_db": m.crest_db}


def crest_picture() -> dict:
    level = 10 ** (-12 / 20)
    clean = [level * v for v in demo.pluck(duration=0.6, rng=random.Random(7))]
    clipped = demo.clipper(clean, demo.drive_for_setting(10))
    a, b = _measured(clean), _measured(clipped)
    paragraphs = [
        "Picture the waveform as a mountain range. The peak is its highest summit. The RMS "
        "is the average height of the land, worked out so that high ground counts for more. "
        "The crest factor is how far the highest summit rises above that average.",

        f"A clean plucked note is all sharp summits rising out of low ground: the string's "
        f"harmonics line up into spikes, then fall away again. In the clean pluck below the "
        f"highest summit is {a['ratio']:.1f} times the average height, a crest factor of "
        f"{a['crest_db']:.1f} dB.",

        f"A clipping pedal acts like a bulldozer that levels every summit at the same height. "
        f"The land becomes flatter and more even, the summits barely clear the average, and "
        f"the crest factor falls: the same pluck through the demo clipper at full gain has a "
        f"peak only {b['ratio']:.2f} times its RMS, {b['crest_db']:.1f} dB. Level everything "
        f"and you get a square wave, a plateau whose summit is the average: 0 dB.",

        "So the crest factor measures how much of the peaks the pedal has shaved off, which "
        "is what clipping is. Turning the volume up or down lifts or lowers the whole range "
        "evenly, so it leaves the crest factor alone; that is why notes recorded at different "
        "levels can be compared. You can often hear it too: a heavily clipped note sounds "
        "dense and even, because more of the wave spends more of its time near full level.",
    ]
    return {"paragraphs": paragraphs, "clean": a, "clipped": b}


def demo_recipe() -> dict:
    harmonics = demo.partials()
    shown = harmonics[:4]
    # Two cycles of the fundamental, every other sample, all phases zero so the
    # build-up is easy to see (the demo itself gives each harmonic a random phase).
    span = 2 / demo.F0
    times = [i / demo.SAMPLE_RATE for i in range(0, int(span * demo.SAMPLE_RATE), 2)]

    def wave(parts: list[dict]) -> list[float]:
        return [math.fsum(p["amplitude"] * math.exp(-t / p["decay_s"])
                          * math.sin(2 * math.pi * p["frequency"] * t) for p in parts)
                for t in times]

    components = [{"label": f"harmonic {p['n']} ({p['frequency']:.0f} Hz)",
                   "points": [[round(t * 1000, 4), round(v, 5)] for t, v in zip(times, wave([p]))]}
                  for p in shown]
    total = {"label": f"sum of all {len(harmonics)} harmonics",
             "points": [[round(t * 1000, 4), round(v, 5)] for t, v in zip(times, wave(harmonics))]}
    paragraphs = [
        "Fourier's discovery is that any repeating wave can be built by adding together sine "
        "waves at whole-number multiples of its basic frequency, its harmonics. A Fourier "
        "transform runs that in one direction, taking a recorded wave apart into its sine "
        "waves. The demo notes are made the other way round, by Fourier synthesis: the sine "
        "waves are chosen and added up.",

        f"Each demo note adds {len(harmonics)} sine waves, at {demo.F0:.0f} Hz, "
        f"{2 * demo.F0:.0f} Hz, {3 * demo.F0:.0f} Hz and so on, each a little sharp, as on a "
        f"real stiff string. Each harmonic's starting size depends on where the string was "
        f"plucked: |sin(nπp)| / n, with p = {demo.PLUCK_POSITION} of the way along, which "
        f"is why some harmonics are strong and others almost missing. Higher harmonics fade "
        f"faster, with a time constant of {demo.DECAY_TIME} s / n^{demo.BRIGHTNESS}, which is why "
        f"a plucked note grows duller as it rings.",

        f"That sum is the clean note. The simulated pedal then clips it, y = tanh(drive × x), "
        f"slightly gentler on the negative side, with the drive rising from 1 at setting 0 to "
        f"{demo.DRIVE_MAX:.0f} at setting 10. In Fourier terms, clipping pours extra strength "
        f"into the higher harmonics, which is what THD measures; the crest factor sees the "
        f"same change directly in the waveform.",

        "Notes you record or upload are not generated or transformed at all: their crest "
        "factor is measured straight from the samples, and no Fourier transform is involved "
        "anywhere in the measurement.",
    ]
    return {"paragraphs": paragraphs,
            "formula": "x(t) = Σₙ Aₙ e^(−t/τₙ) sin(2π fₙ t + φₙ)",
            "components": components, "total": total,
            "harmonics": [{"n": p["n"], "frequency": p["frequency"], "amplitude": p["amplitude"],
                           "decay_s": p["decay_s"]} for p in harmonics[:8]]}
