"""Optional panel fonts, bundled in the plugin's fonts/ folder.

"diablo" is Cinzel (SIL OFL, see fonts/OFL.txt), a free stand-in for
Diablo's Exocet lettering, which can't be redistributed. The frontend
can't load plugin files directly, so it gets @font-face rules with the
fonts inlined as data URLs.
"""

import base64
import os

DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "fonts")

# name -> (CSS family, [(file, unicode-range)])
FONTS = {
    "diablo": ("HHCinzel", [
        ("cinzel-latin.woff2",
         "U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308, U+0329, "
         "U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD"),
        ("cinzel-latin-ext.woff2",
         "U+0100-02BA, U+02BD-02C5, U+02C7-02CC, U+02CE-02D7, U+02DD-02FF, U+0304, U+0308, U+0329, "
         "U+1D00-1DBF, U+1E00-1E9F, U+1EF2-1EFF, U+2020, U+20A0-20AB, U+20AD-20C4, U+2113, U+2C60-2C7F, U+A720-A7FF"),
    ]),
}


def css(name: str, folder: str | None = None) -> str | None:
    """@font-face rules for `name`, or None for the default Steam font."""
    font = FONTS.get(name)
    if not font:
        return None
    family, faces = font
    rules = []
    for file, urange in faces:
        with open(os.path.join(folder or DIR, file), "rb") as f:
            data = base64.b64encode(f.read()).decode()
        rules.append(
            f"@font-face {{ font-family: '{family}'; font-weight: 400 900; "
            f"src: url(data:font/woff2;base64,{data}) format('woff2'); unicode-range: {urange}; }}"
        )
    return "\n".join(rules)
