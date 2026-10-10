"""The design system's promises, checked as code (Phase IV).

The stylesheet is built from colour tokens in two themes. A palette
tweak that quietly drops below readable contrast is exactly the kind
of regression nobody notices until a low-vision user does - so this
test computes WCAG 2 contrast ratios for every text-on-background pair
the app uses, in BOTH themes, and fails under 4.5:1 for text and 3:1
for control boundaries and the focus ring.

Also guards the structural promises: every dark-theme token is also
defined in the light theme (no colour lives only in a media block),
every page carries the shared header navigation, and the layout rules
the 400%-zoom finding depends on are still there.
"""

import re
import unittest

import support  # noqa: F401  (sys.path setup)

CSS_PATH = support.APP_DIR / "static" / "style.css"

TEXT_MIN = 4.5
UI_MIN = 3.0


def _tokens(block):
    return dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\s*;", block))


def load_themes():
    css = CSS_PATH.read_text(encoding="utf-8")
    light_m = re.search(r":root\s*\{(.*?)\}", css, re.S)
    dark_m = re.search(r"prefers-color-scheme:\s*dark\)\s*\{\s*:root\s*\{(.*?)\}",
                       css, re.S)
    light = _tokens(light_m.group(1))
    dark = dict(light)
    dark.update(_tokens(dark_m.group(1)))
    return light, dark, _tokens(dark_m.group(1)), css


def _srgb_to_linear(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hex_colour):
    r, g, b = (int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
    return (0.2126 * _srgb_to_linear(r) + 0.7152 * _srgb_to_linear(g)
            + 0.0722 * _srgb_to_linear(b))


def contrast(a, b):
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


# (foreground token, background token, minimum ratio, where it is used)
PAIRS = [
    ("text", "bg", TEXT_MIN, "body text on the page"),
    ("text", "surface", TEXT_MIN, "text inside cards and fields"),
    ("text", "surface-2", TEXT_MIN, "text on the tab strip and toolbars"),
    ("muted", "bg", TEXT_MIN, "hints on the page"),
    ("muted", "surface", TEXT_MIN, "hints inside cards"),
    ("muted", "surface-2", TEXT_MIN, "raw keys inside setting rows"),
    ("accent", "bg", TEXT_MIN, "links on the page"),
    ("accent", "surface", TEXT_MIN, "links inside cards; the app name"),
    ("accent", "accent-soft", TEXT_MIN, "pressed toggle buttons"),
    ("accent-text", "accent", TEXT_MIN, "primary buttons and the active tab"),
    ("accent-text", "accent-hover", TEXT_MIN, "hovered primary buttons"),
    ("warn", "bg", TEXT_MIN, "warning text on the page"),
    ("warn", "surface", TEXT_MIN, "warning text inside cards"),
    ("warn", "warn-soft", TEXT_MIN, "warning blocks"),
    ("ok", "ok-soft", TEXT_MIN, "the status message"),
    ("ok", "surface", TEXT_MIN, "success text inside cards"),
    ("danger", "surface", TEXT_MIN, "error text"),
    ("border-strong", "surface", UI_MIN, "field and button boundaries"),
    ("focus", "surface", UI_MIN, "the focus ring on cards"),
    ("focus", "bg", UI_MIN, "the focus ring on the page"),
    # The ring never touches a primary button: the :focus-visible rule
    # keeps a 2px surface-coloured gap around the element, so the ring
    # meets the surface or the page, which are the two pairs above.
]


class TestPaletteContrast(unittest.TestCase):

    def check_theme(self, name, tokens):
        failures = []
        for fg, bg, minimum, where in PAIRS:
            self.assertIn(fg, tokens, f"{name}: token --{fg} missing")
            self.assertIn(bg, tokens, f"{name}: token --{bg} missing")
            ratio = contrast(tokens[fg], tokens[bg])
            if ratio < minimum:
                failures.append(f"{name}: --{fg} on --{bg} is {ratio:.2f}:1, "
                                f"needs {minimum}:1 ({where})")
        self.assertEqual(failures, [], "\n".join(failures))

    def test_light_theme_contrast(self):
        light, _dark, _only, _css = load_themes()
        self.check_theme("light", light)

    def test_dark_theme_contrast(self):
        _light, dark, _only, _css = load_themes()
        self.check_theme("dark", dark)

    def test_every_dark_token_exists_in_light(self):
        light, _dark, dark_only, _css = load_themes()
        missing = sorted(set(dark_only) - set(light))
        self.assertEqual(missing, [], "colours defined only for dark mode: "
                         + ", ".join(missing))

    def test_focus_ring_and_wrapping_rules_survive(self):
        _l, _d, _o, css = load_themes()
        self.assertIn(":focus-visible", css)
        self.assertIn("overflow-wrap: anywhere", css)
        # The fixed bottom bar must give way at small heights/widths.
        self.assertRegex(css, r"max-height:\s*30rem")
        # The 3D-view layout stacks by default and only splits wide.
        self.assertRegex(css, r"min-width:\s*70rem")


class TestPageShell(unittest.TestCase):

    def test_every_page_carries_the_shared_navigation(self):
        import webui
        html = webui.page("Any", "<h1>Any</h1>")
        self.assertIn('<nav aria-label="AccessSlice">', html)
        for href in ("/", "/files", "/setup", "/diagnostics"):
            self.assertIn(f'href="{href}"', html)
        # The logo mark is decorative: hidden from screen readers.
        self.assertIn('class="app-mark" aria-hidden="true"', html)
        # Reading width by default, the wide measure only on request.
        self.assertIn("<main>", html)
        self.assertIn('<main class="wide">',
                      webui.page("Any", "", wide=True))

    def test_extra_scripts_load_only_when_asked(self):
        import webui
        plain = webui.page("Any", "")
        self.assertNotIn("viewport.js", plain)
        with_view = webui.page("Any", "", scripts=("three.min.js",
                                                   "viewport.js"))
        self.assertIn('<script src="/static/three.min.js"></script>',
                      with_view)
        self.assertIn('<script src="/static/viewport.js"></script>',
                      with_view)
        self.assertLess(with_view.index("app.js"),
                        with_view.index("three.min.js"))


if __name__ == "__main__":
    unittest.main()
