"""Original print editions. Artwork is rendered offline from the catalog specification."""

import math
import random

from PIL import Image, ImageDraw

STUDIOS = (
    ("Alpine Fold", "Layered mountain silhouettes and high passes"),
    ("Tidal Letterpress", "Coastal editions inspired by the rhythm of waves"),
    ("Linden Herbarium", "Botanical studies in leaf and stem"),
    ("Orbit Almanac", "Celestial charts and imaginary night skies"),
    ("Arcade Editions", "Architectural rhythms in brick and stone"),
    ("Mesa Paperworks", "Desert horizons in warm mineral tones"),
    ("North Sound", "Undulating line studies of open water"),
    ("Sprig & Stem", "Intimate garden studies on textured paper"),
    ("Solstice Press", "Planetary compositions in luminous inks"),
    ("Pavilion Studio", "Modernist arches and courtyard studies"),
)
COLLECTIONS = (
    ("Mountain Landscapes", "Alpine Pass"),
    ("Coastal Studies", "Tide Pool"),
    ("Botanical Prints", "Linden Branch"),
    ("Celestial Maps", "Moon Transit"),
    ("Architectural Prints", "Garden Arcade"),
    ("Desert Landscapes", "Mesa Ridge"),
    ("Ocean Prints", "Sound Passage"),
    ("Garden Studies", "Meadow Stem"),
    ("Planetary Prints", "Orbital Path"),
    ("Modernist Prints", "Open Pavilion"),
    ("Valley Landscapes", "Valley Light"),
    ("River Studies", "River Bend"),
    ("Woodland Prints", "Forest Frond"),
    ("Star Charts", "Evening Star"),
    ("Courtyard Studies", "Inner Court"),
    ("Canyon Prints", "Canyon Rim"),
    ("Estuary Studies", "Reed Channel"),
    ("Wildflower Prints", "Spring Cutting"),
    ("Solar Studies", "Solar Arc"),
    ("City Prints", "City Colonnade"),
)
PALETTES = (
    ("Sage", "#e6ebdd", "#899676", "#354c40", "#e9aa66"),
    ("Ochre", "#f5e4c8", "#ce9a61", "#895443", "#c1573f"),
    ("Indigo", "#dde4ef", "#909ec2", "#35466e", "#c7776c"),
    ("Sea Glass", "#d5e9e2", "#75aba3", "#2e6973", "#eda563"),
    ("Rose", "#f1dfdc", "#ca9a9a", "#85556b", "#db8c55"),
)
FORMATS = ("Art Print", "Postcard Set")


def studio_name(i):
    name, _ = STUDIOS[i % len(STUDIOS)]
    return name if i < len(STUDIOS) else f"{name} Workshop {1 + i // len(STUDIOS)}"


def artwork(motif, palette, seed, size=(480, 600)):
    """Draw the actual edition sold, with a paper border and five families of motifs."""
    rng = random.Random(seed)
    _, paper, mid, dark, accent = palette
    art = Image.new("RGB", (480, 600), paper)
    draw = ImageDraw.Draw(art)
    x, y = rng.randint(80, 350), rng.randint(80, 220)
    if motif % 5 == 0:
        draw.ellipse((x - 43, y - 43, x + 43, y + 43), fill=accent)
        for level, color in enumerate((mid, accent, dark)):
            points = [(j, rng.randint(210, 330) + level * 83) for j in range(-20, 530, 55)]
            draw.polygon(points + [(510, 600), (-20, 600)], fill=color)
    elif motif % 5 == 1:
        for i in range(17):
            phase = rng.uniform(-1, 1)
            points = [
                (j, 120 + i * 26 + 28 * math.sin(j / 79 + phase + i / 4)) for j in range(0, 485, 4)
            ]
            draw.line(points, fill=(dark, mid, accent)[i % 3], width=5 + i % 5)
    elif motif % 5 == 2:
        for _ in range(5):
            root, tip = rng.randint(70, 400), rng.randint(80, 390)
            height = rng.randint(65, 240)
            draw.line((root, 550, tip, height), fill=dark, width=4)
            for j in range(1, 11):
                cx, cy = root + (tip - root) * j / 11, 550 + (height - 550) * j / 11
                reach = (-1) ** j * rng.randint(22, 56)
                draw.polygon(
                    [(cx, cy), (cx + reach, cy - 36), (cx + reach / 2, cy - 8)],
                    fill=(mid, dark)[j % 2],
                )
    elif motif % 5 == 3:
        draw.rectangle((0, 0, 480, 600), fill=dark)
        for _ in range(85):
            sx, sy = rng.randrange(480), rng.randrange(600)
            draw.ellipse((sx, sy, sx + 2, sy + 2), fill=paper)
        for j in range(5):
            d = 45 + j * 49
            draw.ellipse((x - d, y - d / 2, x + d, y + d / 2), outline=mid, width=2)
        draw.ellipse((x - 37, y - 37, x + 37, y + 37), fill=accent)
    else:
        for row in range(3):
            for col in range(3):
                left, top = col * 154 + 10, row * 176 + 45 + rng.randrange(24)
                ink = rng.choice((mid, dark, accent))
                draw.pieslice((left, top, left + 144, top + 144), 180, 360, fill=ink)
                draw.rectangle((left, top + 72, left + 144, top + 154), fill=ink)
                draw.ellipse((left + 47, top + 50, left + 97, top + 102), fill=paper)
                draw.rectangle((left + 47, top + 76, left + 97, top + 155), fill=paper)
    draw.rectangle((0, 0, 479, 599), outline="#faf6ed", width=20)
    return art.resize(size, Image.Resampling.LANCZOS)


def identity(i, banner):
    palette = PALETTES[i % len(PALETTES)]
    size = (900, 280) if banner else (240, 240)
    art = artwork(i, palette, i, size)
    draw = ImageDraw.Draw(art)
    name = studio_name(i)
    label = name.upper() if banner else "".join(word[0] for word in name.split() if word.isalpha())
    draw.rounded_rectangle(
        (12, size[1] - 71, size[0] - 12, size[1] - 12), radius=8, fill=palette[3]
    )
    draw.text((size[0] // 2, size[1] - 41), label, fill="white", anchor="mm", font_size=29)
    return art
