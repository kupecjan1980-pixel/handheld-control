"""Optional panel fonts, bundled in the plugin's fonts/ folder.

The games' own lettering (Diablo's Exocet, id Software's Doom and Quake
logos) can't be redistributed, so each theme uses a free SIL OFL font in
that style; fonts/fonts.json lists the files, and each font's license is
next to it:

- diablo: Cinzel
- matrix: Share Tech Mono
- doom: Metal Mania
- quake: Grenze Gotisch
- fraktur: UnifrakturMaguntia (German blackletter)
- beat: Oswald (Rádio Beat's lettering)

The frontend can't load plugin files directly, so it gets @font-face rules
with the fonts inlined as data URLs.
"""

import base64
import json
import os

DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "fonts")


def manifest(folder: str | None = None) -> dict:
    with open(os.path.join(folder or DIR, "fonts.json")) as f:
        return json.load(f)


def css(name: str, folder: str | None = None) -> str | None:
    """@font-face rules for `name`, or None for the default Steam font."""
    font = manifest(folder).get(name)
    if not font:
        return None
    rules = []
    for face in font["faces"]:
        with open(os.path.join(folder or DIR, face["file"]), "rb") as f:
            data = base64.b64encode(f.read()).decode()
        fmt = face.get("format", "woff2")  # woff2, or a TrueType file as it comes (Oswald)
        mime = "font/ttf" if fmt == "truetype" else f"font/{fmt}"
        rules.append(
            f"@font-face {{ font-family: '{font['family']}'; font-weight: {face['weight']}; "
            f"src: url(data:{mime};base64,{data}) format('{fmt}'); unicode-range: {face['range']}; }}"
        )
    return "\n".join(rules)
