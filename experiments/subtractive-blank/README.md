# Virtual blank subtraction experiment

An isolated CAD construction experiment, not a photo reconstruction or a casting design.
Existing application projects are not changed or replaced.

## Run

From the repository root:

```sh
PYTHONPATH=services MPLCONFIGDIR=/tmp/wheelcam-mpl .venv/bin/python experiments/subtractive-blank/build.py
PYTHONPATH=services MPLCONFIGDIR=/tmp/wheelcam-mpl .venv/bin/python experiments/subtractive-blank/build.py --groups 6 --groove-depth 1 --output artifacts/subtractive-blank-six-v1
```

Use a new output directory to retain earlier candidates. Reusing a directory overwrites its experiment artifacts.

## Construction and artifacts

1. Revolve a continuous hub/front-web/barrel section into one virtual blank.
2. Subtract patterned large and split windows.
3. Subtract paired narrow root slots.
4. Subtract tapered shallow channels following the sloped face.

`blank`, `windows` (including root slots), and `candidate` are exported as STEP and GLB.
`comparison.png` shows front and oblique views of the three stages.
`report.json` records checks and limitations; `recipe.json` records the selected group count,
groove depth, and blank section. Keep **build.py together with recipe.json** to reproduce
the construction: STEP alone does not preserve this editable operation history.
Only group count and groove depth are currently CLI parameters; other cutter dimensions
are explicit constants in the script. This is not yet a general wheel reconstruction engine.

Each subtraction stage must remove positive volume and leave one valid solid.
Each STEP is read back and checked for validity, one solid, and volume agreement.
The final solid must remain inside the original blank.

## Evidence boundary

All dimensions and the five/six-group choices are **design assumptions**, not measurements
or confirmed photo observations. This simplified virtual blank is not a supplied casting.
The preview is a tessellated diagnostic rendering, not a visual-fidelity measurement.
No bolt seats, reconstructed rear face, engineering rim section, fillet continuity,
minimum-wall, structural, casting-process, or CAM-accessibility validation is provided.
The output remains **NOT RELEASED**.

The next useful step is a constrained editable master-sector sketch: fit the large window,
split opening, and root slots against the source photo, review its remaining material,
then pattern that sector. Avoid independently drawing disconnected spokes. Agent edits
should target this recipe/sketch and be accepted only after rebuilding and checking the
solid, rather than replacing the part with arbitrary generated geometry.
