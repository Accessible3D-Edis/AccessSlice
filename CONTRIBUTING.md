# Contributing to AccessSlice

Thank you for helping. AccessSlice exists so that blind and low-vision
makers can prepare 3D prints without anyone's help, and every
contribution is judged by that purpose first.

## The two rules

1. **Accessibility is a shipping requirement, not a feature.** Every
   control labelled and keyboard-operable, nothing that exists only as
   a picture, results announced where the user is, focus never lost.
   The suite's contrast and navigation checks must stay green, and a
   change to anything a screen reader meets needs a screen-reader
   check before it ships.
2. **Every hand-found bug becomes a permanent test first.** Then the
   fix. The suite is stdlib-only, offline and hermetic; keep it so.

## Working on the code

- Run it from the source tree with `python app\accessslice.py` (a
  browser tab) or `python app\accessslice_window.py` (the program's
  own window; needs `pip install pywebview`).
- Run the tests with `python -m unittest discover -s tests -v`.
- Keep settings values as the slicer stores them; never reformat a
  value behind the user's back.
- Plain words on screen. Technical detail goes to the diagnostics log.
- Nothing may leave the user's machine except what they explicitly
  send to their own printer. No telemetry, ever.
- One change per commit, with a message that says what changed and
  why. The history is the project's audit trail.

## Licence of contributions

AccessSlice is licensed under the GNU General Public License,
version 3 (see `LICENSE`). By contributing you agree to the following,
which is the whole of the contributor agreement:

1. **Your contribution is yours to give.** You wrote it, or you have
   the right to submit it under these terms.
2. **It is licensed under GPL-3.0**, like the rest of AccessSlice.
3. **You grant the project's copyright holder, Edis Adilovic, a
   perpetual, worldwide, non-exclusive, royalty-free right to also
   license your contribution under other terms**, including a
   commercial licence. This keeps the door open for the project to
   fund itself through dual licensing while the public version stays
   GPL-3.0 forever. You keep every right to your own work.

You accept these terms by submitting a pull request or a patch. If
you cannot accept them, say so in the pull request and we will talk;
an issue report or a bug description needs no agreement at all.

## Reporting a problem

Say what you pressed, what your screen reader said, and what you
expected, and paste the end of the Diagnostics page (Home, then
Diagnostics). That is enough to fix nearly anything. Never paste your
printer's address, password or serial number; the Diagnostics log
does not contain them, but a project file might.
