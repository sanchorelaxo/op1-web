# White-corrected panel reference plates

Source: three phone photos of the user's own OP-1 (2048x2048 each), shot on a dark
desk mat. The ABS has yellowed with age, so the originals show "white" as beige and
the background as whatever the desk was.

Regenerated with:

    /usr/bin/python3 tools/whiten_photos.py <photo>.jpg -o refs/print/white --lift 1.0 --knee 0.62

What the script does, per image:

1. Segments the device (Otsu threshold, 5 px median to kill speckle, then each row
   filled between its leftmost and rightmost bright pixel) and composites it onto
   pure white, so no desk, shadow or bag survives.
2. Measures the plastic's own tint from the low-saturation bright pixels inside the
   device, then divides that tint out in linear light. That is what removes the
   yellowing rather than a fixed guess.
3. Eases low-saturation pixels above 0.62 up to true white with a smoothstep, so the
   keys land on white. Black print and the four encoder caps keep their values.

Measured white points (sRGB 0..1) and the gains applied:

| plate | measured plastic | gains R,G,B |
|---|---|---|
| a | 0.682 0.659 0.663 | 1.466 1.518 1.509 |
| b | 0.682 0.663 0.686 | 1.466 1.509 1.457 |
| c | 0.659 0.647 0.702 | 1.518 1.545 1.425 |

Corners are pure `srgb(255,255,255)`; the only non-white corner pixel in plate b is
the device itself, because that photo is cropped through the left edge.

## Files

- `op1_panel_white_a.png` - full panel, speaker/volume top left, mode keys and 1-4.
- `op1_panel_white_b.png` - knobs plus the numbered row 1-8, closest read of the print.
- `op1_panel_white_c.png` - knobs 4-8, COM/album column, right edge with the OP-1 logo.
- `op1_panel_white_sheet.png` - the three side by side, for quick comparison.

## Print read off these plates

Numbered row under the encoders, black number over a coloured secondary symbol:

| key | number | secondary | ink |
|---|---|---|---|
| 1 | 1 | IN | green |
| 2 | 2 | OUT | green |
| 3 | 3 | loop / arrow | green |
| 4 | 4 | tape reel | orange |
| 5 | 5 | reverse R | orange |
| 6 | 6 | three dots | orange |
| 7 | 7 | M1 | black |
| 8 | 8 | M2 | black |

Encoders left to right: blue, green, white, orange. Right column: album key with an
orange key icon, then COM in black. Main mode keys carry a waveform (synth), circle
(drum), tape loop (tape), bar graph (mixer).
