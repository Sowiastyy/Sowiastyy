"""Draws the profile README's animated project cards (assets/*.gif) from live numbers.

    python .github/cards/build.py            fetch the numbers, then draw
    python .github/cards/build.py --offline  draw from stats.json as it is

    python .github/cards/build.py --nomads   only Nomads' card, with how many play now

The cards workflow runs this once a day, and with --nomads every hour: Nomads'
card says how many play now, so it is not kept here but on the cards branch,
one commit pushed over the last (assets/nomads.gif is only drawn here).
Drawing is deterministic: the same numbers give byte-identical GIFs, so a
quiet day commits nothing.
"""
from __future__ import annotations

import json
import math
import os
import sys
import urllib.request
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont

CARDS = Path(__file__).resolve().parent
ASSETS = CARDS.parent.parent / "assets"
FONTS = CARDS / "fonts"
SRC = CARDS / "src"
STATS = CARDS / "stats.json"

S = 3  # pixel-art layers are drawn at 1/S and blown up with nearest neighbour
W = 320  # base width: 960 px in the GIF

BG = (19, 19, 28)
GRID = (30, 30, 44)
INK = (232, 230, 240)
MUTED = (139, 138, 168)
GREEN = (27, 217, 106)
RED = (235, 87, 87)
CURSEFORGE = (241, 100, 54)


# ---------------------------------------------------------------- numbers


def get_json(url: str):
    headers = {"User-Agent": "Sowiastyy-profile-readme"}
    token = os.environ.get("GITHUB_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as r:
        return json.load(r)


def fetch_nomads() -> dict:
    return {"players": get_json("https://nomads.sowiasty.dev/status")["players"]}


def fetch_modrinth() -> dict:
    p = get_json("https://api.modrinth.com/v2/project/head-lab")
    return {
        "modrinth": p["downloads"],
        "followers": p["followers"],
        "mc":[p["game_versions"][0], p["game_versions"][-1]],
    }


def fetch_curseforge() -> dict:
    # CurseForge's own API wants a key; cfwidget mirrors the public numbers without one
    p = get_json("https://api.cfwidget.com/minecraft/mc-mods/head-lab")
    return {"curseforge": p["downloads"]["total"]}


def fetch_fleet() -> dict:
    api = "https://api.github.com/repos/Sowiastyy/claude-fleet"
    repo = get_json(api)
    releases, page = [], 1
    while True:
        batch = get_json(f"{api}/releases?per_page=100&page={page}")
        releases += batch
        if len(batch) < 100:
            break
        page += 1
    return {
        "stars": repo["stargazers_count"],
        "forks": repo["forks_count"],
        "releases": len(releases),
        "downloads": sum(a["download_count"] for r in releases for a in r["assets"]),
        "latest": releases[0]["tag_name"] if releases else "",
    }


def load_stats(offline: bool, only: str | None = None) -> dict:
    """The numbers in stats.json, fetched afresh unless `offline`: all of them, or `only` one card's."""
    stats = json.loads(STATS.read_text()) if STATS.exists() else {}
    if offline:
        return stats
    for key, fetch in (("nomads", fetch_nomads), ("headlab", fetch_modrinth), ("headlab", fetch_curseforge), ("fleet", fetch_fleet)):
        if only and key != only:
            continue
        try:
            stats.setdefault(key, {}).update(fetch())
        except Exception as e:  # a source that is down keeps yesterday's numbers
            print(f"warning: {fetch.__name__}: {e}", file=sys.stderr)
    STATS.write_text(json.dumps(stats, indent=2) + "\n")
    return stats


def compact(n: int) -> str:
    return f"{n / 1000:.1f}K".replace(".0K", "K") if n >= 1000 else str(n)


# ---------------------------------------------------------------- pixel art


def pixel_font(size: int = 8, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / f"Silkscreen-{'Bold' if bold else 'Regular'}.ttf"), size)


PX = pixel_font()
PX_BIG = pixel_font(16, bold=True)

ICONS = {
    "down": ["..###..", "..###..", "..###..", "#######", ".#####.", "..###..", "...#..."],
    "heart": [".......", ".##.##.", "#######", "#######", ".#####.", "..###..", "...#..."],
    "grass": ["ggggggg", "gGgggGg", "dgdddgd", "ddddddd", "dDdddDd", "ddddddd", "ddDdddd"],
    "check": [".......", "......#", ".....##", "#...##.", "##.##..", ".###...", "..#...."],
}

# Minecraft's item tooltip: near-black purple, a border fading from violet to
# indigo, text with a shadow a quarter as bright one pixel down and right
TT_BG = (16, 0, 16)
TT_TOP, TT_BOTTOM = (36, 0, 90), (23, 0, 50)
MC_YELLOW, MC_GREY, MC_WHITE = (255, 255, 85), (170, 170, 170), (255, 255, 255)
ICON_COLORS = {"g": (93, 168, 64), "G": (64, 128, 44), "d": (134, 96, 67), "D": (98, 68, 46)}


def canvas(w: int, h: int, bg=BG):
    im = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(im)
    d.fontmode = "1"  # no anti-aliasing: Silkscreen at multiples of 8 lands on whole pixels
    return im, d


def scene(h: int):
    """The cards' backdrop: flat night blue with a dot every 8 pixels."""
    im, d = canvas(W, h)
    for y in range(4, h, 8):
        for x in range(4, W, 8):
            d.point((x, y), fill=GRID)
    return im, d


def put_text(d: ImageDraw.ImageDraw, x: float, y: int, s: str, fill, font=PX) -> float:
    """Draws s with its cap top at y (Silkscreen's glyphs sit a few pixels below the pen); returns the pen's new x."""
    d.text((x, y - font.getbbox("A")[1]), s, fill=fill, font=font)
    return x + font.getlength(s)


def put_icon(d: ImageDraw.ImageDraw, name: str, x: float, y: int, fill, flat: bool = False) -> None:
    """flat paints every pixel in fill, for a shadow, even the ones the icon has its own colours for."""
    for j, row in enumerate(ICONS[name]):
        for i, ch in enumerate(row):
            if ch != ".":
                d.point((x + i, y + j), fill=fill if flat else ICON_COLORS.get(ch, fill))


def tooltip(d: ImageDraw.ImageDraw, x0: int, y0: int, lines: list[tuple[str | None, list[tuple[str, tuple]]]]) -> int:
    """A Minecraft item tooltip at (x0, y0): lines of (icon, [(text, colour), ...]). Returns its bottom edge."""
    def width(icon, spans):  # an icon of "" holds an icon's room empty, to indent a line under the one above
        return (10 if icon is not None else 0) + sum(PX.getlength(s) for s, _ in spans)

    x1 = x0 + 8 + max(int(width(*line)) for line in lines)
    y1 = y0 + 6 + 10 * len(lines)
    d.rectangle([x0 + 1, y0, x1 - 1, y1], fill=TT_BG)  # the corners stay cut, as in the game
    d.rectangle([x0, y0 + 1, x1, y1 - 1], fill=TT_BG)
    for y in range(y0 + 1, y1):
        t = round((y - y0 - 1) / (y1 - y0 - 2) * 3) / 3  # four steps of the fade, not one colour a row
        c = tuple(round(a + (b - a) * t) for a, b in zip(TT_TOP, TT_BOTTOM))
        d.point((x0 + 1, y), fill=c)
        d.point((x1 - 1, y), fill=c)
    d.line([(x0 + 1, y0 + 1), (x1 - 1, y0 + 1)], fill=TT_TOP)
    d.line([(x0 + 1, y1 - 1), (x1 - 1, y1 - 1)], fill=TT_BOTTOM)
    for k, (icon, spans) in enumerate(lines):
        x, y = x0 + 4, y0 + 4 + 10 * k
        if icon:  # 7 pixels tall against 5 of capitals: one up so they share a middle
            put_icon(d, icon, x + 1, y, tuple(v // 4 for v in MC_GREY), flat=True)
            put_icon(d, icon, x, y - 1, MC_GREY)
        if icon is not None:
            x += 10
        for s, c in spans:
            put_text(d, x + 1, y + 1, s, tuple(v // 4 for v in c))
            x = put_text(d, x, y, s, c)
    return y1


def loop_palette(frames: list[Image.Image]) -> Image.Image:
    """The frames' own colours if they fit in 256, else 256 picked by median cut from a few frames."""
    colors = set()
    for fr in frames:
        colors.update(c for _, c in fr.getcolors(1 << 24))
        if len(colors) > 256:
            break
    if len(colors) <= 256:
        flat = [v for c in sorted(colors) for v in c]
        pal = Image.new("P", (1, 1))
        pal.putpalette(flat + [0] * (768 - len(flat)))
        return pal
    picks = [frames[i * (len(frames) - 1) // 3] for i in range(4)]
    mosaic = Image.new("RGB", (frames[0].width, frames[0].height * len(picks)))
    for i, fr in enumerate(picks):
        mosaic.paste(fr, (0, i * fr.height))
    return mosaic.quantize(256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)


def save_gif(frames: list[Image.Image], path: Path, ms: int, palette: Image.Image | None = None) -> None:
    """One palette for the whole loop (colours never flicker between frames), no dithering (flat stays flat).

    A card may bring its own `palette`; otherwise it is the frames' (`loop_palette`).
    """
    pal = loop_palette(frames) if palette is None else palette
    out = [fr.quantize(palette=pal, dither=Image.Dither.NONE) for fr in frames]
    out[0].save(path, save_all=True, append_images=out[1:], duration=ms, loop=0)
    print(f"{path.name}: {len(frames)} frames, {path.stat().st_size // 1024} KB")


# ---------------------------------------------------------------- Nomads

# The game's own sprites, which the game draws in code: src/nomads-sprites.png and
# its frame table are written by the game's scripts/profile-sprites.ts (`npm run
# profile-sprites` in the game, then copy dist/profile/* here, when its art changes).
# The flame is the campfire's automaton, NM_FRAMES steps of it that loop; the light is
# the game's night (render/light.ts) and its campfire glow (render/world/fire.ts).
NM_MS = 70  # about the game's 15 flame steps a second
NM_FRAMES = 120  # as exported: room in the loop for the rabbit
G = 4  # the scene's game pixels to a GIF pixel; the text keeps the cards' S
NM_W, NM_H = W * S // G, 120 * S // G  # 240 x 90 game pixels
FIRE = (164, 50)  # the campfire's foot
MEADOW = [(0x6A, 0x9D, 0x47), (0x5B, 0x8F, 0x3D), (0x4D, 0x7C, 0x34), (0x3F, 0x6A, 0x2B)]
NIGHT_FOG = (56, 64, 106)  # the game's night sky dimmed, a little less than its fog of war
LAMPLIGHT = (240, 206, 178)  # what the tribe sees by at night: the night sky lifted towards the fire's warmth
POOL = (78, 38)  # how far the fire lights the ground either way, flattened as the ground is seen
GLOW_TINT, GLOW = (255, 138, 42), 0.42
FLAME_TALL = 22  # as exported
KING_IDLE = 4  # the king's horse breathing: its idle frames, as exported
# a spark cools through the fire's own colours (art/sprites/items.ts FIRE_RAMP)
EMBER = [(0xFF, 0xFB, 0xE8), (0xFF, 0xF0, 0x7A), (0xFF, 0xC4, 0x3C), (0xF7, 0xA5, 0x31), (0xD9, 0x50, 0x2F), (0x9C, 0x2C, 0x1F)]

# the menu's look (client/index.html): gold title, green PLAY with a darker lip under it
GOLD, NM_INK, NM_SUB = (242, 201, 76), (30, 26, 36), (200, 192, 176)
PLAY_UP, PLAY_DOWN, PLAY_LIP = (116, 184, 58), (134, 201, 74), (70, 117, 34)
# the tribe sitting round the back half of the fire, open to the viewer: which kin (as
# exported), where, which way it faces (0 south .. 4 north, the game's), mirrored
CIRCLE = [
    (4, 150, 41, 1, False),
    (3, 178, 40, 1, True),
]
KING_AT, KING_DIR = (198, 58), 2  # the king on his horse, side on, facing west (east mirrored): to the fire
TREES = [  # name, x, y: a dark wood round the glade, thickest behind the camp
    ("tree_1_3_1", 230, 36), ("tree_0_2_1", 138, 28), ("tree_2_2_3", 170, 20), ("tree_3_2_0", 112, 22),
    ("tree_1_2_2", 204, 26), ("tree_0_2_2", 84, 16), ("tree_1_2_0", 56, 12), ("tree_0_1_2", 26, 14),
    ("tree_1_1_1", 234, 80),
]
TUFTS = [("grass_tall_1_0", 122, 46), ("grass_tall_0_2", 186, 72), ("grass_tall_0_1", 156, 80), ("grass_short_0_1", 176, 66),
         ("grass_short_0_0", 132, 40), ("grass_short_0_2", 214, 50), ("grass_tall_0_0", 214, 84)]
FIREFLIES = [(96, 14, 0), (118, 52, 1), (226, 10, 2), (92, 40, 3), (226, 64, 4)]  # where each drifts about

# The archer, the lowest of the ring, and a rabbit that comes up out of the dark off to the side to
# a tuft of grass. He stands, walks a few steps towards it, draws as a bow is drawn in the game
# (art/figures/frames.ts ACTIONS.draw: 0, 0.2, 0.5 s, the arrow loosed at BOW_DRAW_TIME, 0.8 s) and
# shoots it; it leaves its meat, which he walks over to and picks up as a kin picks up what lies
# about (the bend, PICKUP_TIME), and he walks back to his seat. His sight lifts the night round him
# as a kin's does (render/light.ts). Frames of the loop.
ARCHER = 1
SEAT, SHOT, PICK_AT = (140, 58), (126, 66), (110, 75)  # where he sits, shoots from, bends for the meat
RABBIT_FROM, RABBIT_AT = (72, 92), (104, 76)  # out of the dark at the bottom, up to the grass
GRASS_AT = (97, 77)
RABBIT_IN, RABBIT_SITS = 12, 40
UP, WALK_OUT, DRAWS, LOOSES, HITS, WALK_ON, BENDS, TAKEN, WALK_BACK, SITS = 44, 47, 53, 64, 67, 72, 78, 80, 82, 94
DRAW_FRAMES = [(0, "draw0"), (3, "draw1"), (7, "draw2")]  # from DRAWS: 0, 0.2, 0.5 s
WALK_STEP = 0.12  # s a walking frame (render/world/draw/kin.ts)
BOW_HAND = (-6, -8)  # where the arrow leaves him, aiming, from his feet
SIGHT = (56, 30)  # how far his sight lifts the night round him, flattened as the ground is seen
# the game's sprite directions by octant from east, clockwise (art/figures/frames.ts OCTANT): (dir, mirrored)
OCTANT = [(2, False), (1, False), (0, False), (1, True), (2, True), (3, True), (4, False), (3, False)]

# Under the title how many play now, and lower down PLAY with a kinsman hopping on it as the
# tribesmen on the game's loading curtain do (lobby/loading-row.ts and its hop-* keyframes):
# standing, crouched with his hands down, up with his arms up 11 px, landing in the crouch. He
# stands in the middle of it with a faint shadow under him; each landing presses it and each jump
# lets it up. The button, his shadow and he are on the scene's grid, the word on it on the text's,
# and he over the word; the curtain's 0.8 s is 12 frames here, ten hops a loop.
HOPPER, HOP_FRAMES = 5, 12
HOP_POSE = [(0, "idle0"), (0.12, "bend"), (0.22, "raise1"), (0.62, "bend"), (0.72, "idle0")]
HOP_LIFT = [(0, 0), (0.24, 4), (0.29, 8), (0.35, 10), (0.43, 11), (0.51, 10), (0.56, 8), (0.60, 4), (0.62, 0)]
BUTTON = (9, 70, 61, 83)  # game px: PLAY, its lip a pixel under it
STAND_ON = 77  # the row of it his feet are on: its middle
PLAY_SPACING = 2  # px between its letters, as the menu spaces them


def noise(x: int, y: int, seed: int) -> float:
    """The same speckle every run: a hash of the pixel, 0..1."""
    h = (x * 374761393 + y * 668265263 + seed * 2246822519) & 0xFFFFFFFF
    h = ((h ^ (h >> 13)) * 1274126177) & 0xFFFFFFFF
    return (h ^ (h >> 16)) / 0xFFFFFFFF


class Sheet:
    """The exported sprites by name, each stamped with its anchor on the spot asked for."""

    def __init__(self):
        self.im = Image.open(SRC / "nomads-sprites.png").convert("RGBA")
        self.frames = json.loads((SRC / "nomads-sprites.json").read_text())
        self.cache = {}

    def get(self, name: str, flip: bool = False, tint=None):
        if (name, flip, tint) not in self.cache:
            f = self.frames[name]
            im = self.im.crop((f["x"], f["y"], f["x"] + f["w"], f["y"] + f["h"]))
            im = im.transpose(Image.FLIP_LEFT_RIGHT) if flip else im
            if tint:  # multiplied, as the game tints a sprite
                im = ImageChops.multiply(im, Image.new("RGBA", im.size, (*tint, 255)))
            ax = f["w"] - f["ax"] if flip else f["ax"]
            self.cache[name, flip, tint] = (im, ax, f["ay"])
        return self.cache[name, flip, tint]

    def turned(self, name: str, degrees: float) -> str:
        """`name` turned `degrees` clockwise about its anchor (as a sprite's rotation in the game), kept as a sprite of its own."""
        key = f"{name}@{round(degrees)}"
        if key not in self.frames:
            im, ax, ay = self.get(name)
            big = Image.new("RGBA", (im.width * 2 + 2, im.height * 2 + 2))
            cx, cy = big.width // 2, big.height // 2
            big.alpha_composite(im, (round(cx - ax), round(cy - ay)))
            big = big.rotate(-round(degrees), Image.NEAREST, center=(cx, cy))
            box = big.getbbox()
            self.frames[key] = {"x": 0, "y": 0, "w": 0, "h": 0, "ax": cx - box[0], "ay": cy - box[1]}
            self.cache[key, False, None] = (big.crop(box), cx - box[0], cy - box[1])
        return key

    def put(self, dst: Image.Image, name: str, x: float, y: float, flip: bool = False, tint=None) -> None:
        im, ax, ay = self.get(name, flip, tint)
        x0, y0 = round(x - ax), round(y - ay)
        # alpha_composite takes no negative destination: cut off what hangs over the top or left
        cx, cy = max(0, -x0), max(0, -y0)
        if cx < im.width and cy < im.height:
            dst.alpha_composite(im, (x0 + cx, y0 + cy), (cx, cy))


def smoothstep(a: float, b: float, x: float) -> float:
    k = min(1.0, max(0.0, (x - a) / (b - a)))
    return k * k * (3 - 2 * k)


def stepped(keys: list, t: float):
    """What the keyframes say at `t`, held from one to the next as `steps(1, end)` holds them."""
    return [v for at, v in keys if at <= t][-1]


def nm_ground() -> Image.Image:
    """The glade's meadow, darker away from the camp, speckled with lighter grass."""
    im = Image.new("RGBA", (NM_W, NM_H))
    px = im.load()
    for y in range(NM_H):
        for x in range(NM_W):
            dx, dy = (x - FIRE[0]) / 110, (y - FIRE[1]) / 60
            d = dx * dx + dy * dy + (noise(x, y, 7) - 0.5) * 0.18
            c = MEADOW[0 if d < 0.2 else 1 if d < 0.55 else 2 if d < 1.0 else 3]
            px[x, y] = (*((0x7A, 0xAD, 0x52) if noise(x, y, 3) < 0.04 else c), 255)
    return im


def nm_sight() -> Image.Image:
    """The night lifted round a kin, centred: what nm_light lays round the archer wherever he is."""
    im = Image.new("L", (2 * SIGHT[0] + 1, 2 * SIGHT[1] + 1))
    px = im.load()
    for y in range(im.height):
        for x in range(im.width):
            d = math.hypot((x - SIGHT[0]) / SIGHT[0], (y - SIGHT[1]) / SIGHT[1])
            px[x, y] = round(220 * (1 - smoothstep(0.3, 1.0, d)))
    return im


def nm_masks():
    """The fire's pool of light on the ground, the vignette and the glow's shape, 0..255 a pixel."""
    pool, vignette, glow = (Image.new("L", (NM_W, NM_H)) for _ in range(3))
    pp, vp, gp = pool.load(), vignette.load(), glow.load()
    h = FLAME_TALL
    for y in range(NM_H):
        for x in range(NM_W):
            lx, ly = x - FIRE[0] + 0.5, y - FIRE[1] + 0.5
            d = math.hypot(lx / POOL[0], ly / POOL[1])
            pp[x, y] = round(255 * (1 - smoothstep(0.35, 1.0, d)))
            vx, vy = (x + 0.5) / NM_W - 0.5, ((y + 0.5) / NM_H - 0.5) * 0.85
            vp[x, y] = round(255 * (0.6 + 0.4 * smoothstep(0.62, 0.22, math.hypot(vx, vy))))
            # the campfire glow's shader: a halo round the flame and a wide, flat one on the ground
            halo = max(1 - math.hypot(lx / (h * 1.2), (ly + h * 0.45) / (h * 1.05)), 0) ** 2.2
            ground = max(1 - math.hypot(lx / (h * 2.1), ly / (h * 0.8)), 0) ** 1.7 * 0.75
            gp[x, y] = round(255 * max(halo, ground))
    return pool, vignette, glow, nm_sight()


def facing(dx: float, dy: float):
    """The sprite direction and mirroring for a heading, as the game picks them (spriteDir)."""
    return OCTANT[round(math.atan2(dy, dx) / (math.pi / 4)) % 8]


def archer_at(f: int):
    """Where the archer is in frame `f`, and his sprite: (x, y, name, mirrored)."""
    (sx, sy), (hx, hy), (px, py), (rx, ry) = SEAT, SHOT, PICK_AT, RABBIT_AT
    if f < UP or f >= SITS:
        return sx, sy, f"kin_{ARCHER}_sit_2", False  # by the fire, facing it
    walks = [(WALK_OUT, DRAWS, SEAT, SHOT), (WALK_ON, BENDS, SHOT, PICK_AT), (WALK_BACK, SITS, PICK_AT, SEAT)]
    for start, end, (ax, ay), (bx, by) in walks:
        if start <= f < end:
            u = (f - start) / (end - start)
            d, flip = facing(bx - ax, by - ay)
            step = int((f - start) * NM_MS / 1000 / WALK_STEP) % 4
            return ax + (bx - ax) * u, ay + (by - ay) * u, f"kin_{ARCHER}_walk{step}_{d}", flip
    if BENDS <= f < WALK_BACK:
        d, flip = facing(rx - px, ry - py)
        return px, py, f"kin_{ARCHER}_bend_{d}", flip
    x, y = (sx, sy) if f < WALK_OUT else (hx, hy)
    d, flip = facing(rx - x, ry - y)
    frame = "idle0"
    for at, name in DRAW_FRAMES:
        if DRAWS + at <= f < LOOSES:
            frame = name
    return x, y, f"kin_{ARCHER}_{frame}_{d}", flip


def nm_hunt(sheet: Sheet, im: Image.Image, f: int, stand: list) -> None:
    """The archer and the rabbit, frame `f`: what stands goes into `stand`, the shadows straight onto `im`."""
    ax, ay, archer, flip = archer_at(f)
    sheet.put(im, "shadow_m", ax, ay)
    stand.append((ay, lambda: sheet.put(im, archer, ax, ay, flip)))
    (fx, fy), (rx, ry) = RABBIT_FROM, RABBIT_AT
    if RABBIT_IN <= f < HITS:  # hopping in, then sitting by the grass, turned to it
        u = min(1.0, (f - RABBIT_IN) / (RABBIT_SITS - RABBIT_IN))
        x, y = fx + (rx - fx) * u, fy + (ry - fy) * u
        z = 0 if f >= RABBIT_SITS else abs(math.sin(u * math.pi * 6)) * 3
        rabbit = "rabbit_0" if z < 1 else "rabbit_1"
        tint = (255, 128, 128) if f == HITS - 1 else None  # the arrow lands: hurt, red for a moment
        sheet.put(im, "shadow_s", x, y)
        stand.append((y, lambda: sheet.put(im, rabbit, x, y - round(z), f >= RABBIT_SITS, tint)))
    if LOOSES <= f < HITS:  # the arrow, straight at it, turned the way it flies as the game turns it
        u = (f - LOOSES + 1) / (HITS - LOOSES)
        sx, sy = SHOT[0] + BOW_HAND[0], SHOT[1] + BOW_HAND[1]
        tx, ty = rx, ry - 4
        arrow = sheet.turned("arrow", math.degrees(math.atan2(ty - sy, tx - sx)))
        stand.append((ry + 1, lambda: sheet.put(im, arrow, sx + (tx - sx) * u, sy + (ty - sy) * u)))
    if HITS <= f < TAKEN:  # its meat, bobbing as a drop does, until he picks it up
        bob = round(math.sin((f - HITS) * NM_MS / 1000 * 4 + rx) * 0.6)
        stand.append((ry, lambda: sheet.put(im, "res_0", rx, ry - 1 + bob)))


def nm_blood(im: Image.Image, f: int) -> None:
    """The blood a rabbit's death throws up (render/fx.ts: six specks, falling back)."""
    age = (f - HITS) * NM_MS / 1000
    if not 0 <= age < 0.5:
        return
    px = im.load()
    rx, ry = RABBIT_AT
    for i in range(6):
        a = math.pi * (0.15 + 0.7 * noise(i, 5, 41))
        v = 40 + 10 * noise(i, 6, 42)
        x = rx + math.cos(a) * v * age * (1 if i % 2 else -1)
        y = ry - 3 - math.sin(a) * v * age + 120 * age * age
        for dx in (0, 1):
            for dy in (0, 1):
                if 0 <= x + dx < NM_W and 0 <= y + dy < NM_H:
                    px[int(x + dx), int(y + dy)] = (176, 32, 40)


def nm_scene(sheet: Sheet, ground: Image.Image, f: int) -> Image.Image:
    im = ground.copy()
    stand = [(y, lambda n=n, x=x, y=y: sheet.put(im, n, x, y)) for n, x, y in TREES + TUFTS + [("grass_tall_1_1", *GRASS_AT)]]
    for n, x, y in TREES:
        sheet.put(im, "shadow_tree", x, y)
    for k, x, y, d, flip in CIRCLE:
        sheet.put(im, "shadow_m", x, y)
        stand.append((y, lambda k=k, x=x, y=y, d=d, flip=flip: sheet.put(im, f"kin_{k}_sit_{d}", x, y, flip)))
    nm_hunt(sheet, im, f, stand)
    # their king on his horse beside them, turned to the fire, the horse breathing as in the game
    sheet.put(im, "shadow_l", *KING_AT)
    king = f"king_idle{f * KING_IDLE // NM_FRAMES}_{KING_DIR}"
    stand.append((KING_AT[1], lambda: sheet.put(im, king, *KING_AT, True)))
    stand.append((FIRE[1], lambda: sheet.put(im, "campfire", *FIRE)))
    stand.append((FIRE[1] + 0.5, lambda: sheet.put(im, f"flame_{f % NM_FRAMES}", FIRE[0], FIRE[1] - 1)))
    for _, draw in sorted(stand, key=lambda s: s[0]):
        draw()
    return im


def nm_light(scene: Image.Image, masks, f: int) -> Image.Image:
    pool, vignette, glow, sight = masks
    t = 2 * math.pi * f / NM_FRAMES  # whole turns a loop, so the flicker wraps with it
    flick = 0.86 + 0.09 * math.sin(14 * t + 1.3) + 0.05 * math.sin(34 * t + 3.9)
    light = pool.point(lambda v: v * (0.92 + 0.08 * flick))
    ax, ay, _, _ = archer_at(f)
    seen = Image.new("L", scene.size)
    seen.paste(sight, (round(ax) - SIGHT[0], round(ay) - 3 - SIGHT[1]))
    light = ImageChops.lighter(light, seen)
    lit = Image.composite(Image.new("RGB", scene.size, LAMPLIGHT), Image.new("RGB", scene.size, NIGHT_FOG), light)
    lit = ImageChops.multiply(lit, Image.merge("RGB", [vignette] * 3))
    out = ImageChops.multiply(scene.convert("RGB"), lit)
    k = GLOW * flick
    g = glow.point(lambda v: v * k)
    return ImageChops.add(out, ImageChops.multiply(Image.new("RGB", scene.size, GLOW_TINT), Image.merge("RGB", [g] * 3)))


def nm_embers(im: Image.Image, f: int) -> None:
    """Sparks rising off the fire, each on a loop of its own that fits the card's."""
    px = im.load()
    for i in range(18):
        life = 14 + int(noise(i, 1, 21) * 12)
        age = (f + i * 7) % NM_FRAMES
        if age >= life:
            continue
        u = age / life
        x = FIRE[0] + (noise(i, 2, 22) - 0.5) * 8 + math.sin(u * 5 + i) * 3 * u
        y = FIRE[1] - 6 - age * (0.9 + noise(i, 3, 23) * 0.6)
        if 0 <= x < NM_W and 0 <= y < NM_H:
            px[int(x), int(y)] = EMBER[min(len(EMBER) - 1, int(u * len(EMBER)))]


def nm_fireflies(im: Image.Image, f: int) -> None:
    """Fireflies in the dark round the glade: a slow loop each, glowing up and fading out."""
    px = im.load()
    for x0, y0, i in FIREFLIES:
        t = 2 * math.pi * (f / NM_FRAMES + noise(i, 4, 31))
        glow = max(0.0, math.sin(t * 4 + i))  # four times a loop, dark half the time
        if glow < 0.15:
            continue
        x = round(x0 + 6 * math.sin(2 * t + i) + 2 * math.sin(6 * t))
        y = round(y0 + 3 * math.sin(4 * t + i * 1.7))
        for dx, dy, k in ((0, 0, 1.0), (-1, 0, 0.35), (1, 0, 0.35), (0, -1, 0.35), (0, 1, 0.35)):
            if 0 <= x + dx < NM_W and 0 <= y + dy < NM_H:
                r, g, b = px[x + dx, y + dy]
                a = glow * k
                px[x + dx, y + dy] = (min(255, round(r + 230 * a)), min(255, round(g + 250 * a)), min(255, round(b + 120 * a)))


def hop_at(f: int):
    """The hopper's pose, how high he is, and whether his landing holds PLAY down."""
    t = f % HOP_FRAMES / HOP_FRAMES
    pose = stepped(HOP_POSE, t)
    return pose, stepped(HOP_LIFT, t), pose == "bend"


def nm_play(sheet: Sheet, im: Image.Image, f: int) -> None:
    """PLAY and the hopper's shadow on it, on the scene's grid over the night (they are no part of it):
    the game's shadow, smaller while he is high, as the curtain's shadow shrinks under him."""
    x0, y0, x1, y1 = BUTTON
    _, lift, down = hop_at(f)
    d = ImageDraw.Draw(im)
    d.rectangle([x0, y0 + 1, x1, y1 + 1], fill=PLAY_LIP)
    d.rectangle([x0, y0 + down, x1, y1 + down], fill=PLAY_DOWN if down else PLAY_UP, outline=NM_INK)
    lit = im.convert("RGBA")
    sheet.put(lit, "shadow_s" if lift >= 8 else "shadow_m", (x0 + x1 + 1) / 2, STAND_ON + down)
    im.paste(lit.convert("RGB"))


def nm_hopper(sheet: Sheet, f: int) -> Image.Image:
    """The kinsman hopping on PLAY, on the scene's grid, to be laid over the word on it."""
    im = Image.new("RGBA", (NM_W, NM_H))
    pose, lift, down = hop_at(f)
    sheet.put(im, f"kin_{HOPPER}_{pose}_0", (BUTTON[0] + BUTTON[2] + 1) / 2, STAND_ON + down - lift)
    return im


def nm_text(f: int, players: int | None) -> Image.Image:
    """The title, how many play now (`players` None: the line left out) and the word on PLAY, on the cards' S grid."""
    im = Image.new("RGBA", (W, 120))
    d = ImageDraw.Draw(im)
    d.fontmode = "1"
    title, big = pixel_font(24, bold=True), pixel_font(16, bold=True)
    put_text(d, 13, 13, "NOMADS", NM_INK, title)  # the title's hard shadow, as the menu's
    put_text(d, 12, 12, "NOMADS", GOLD, title)
    put_text(d, 12, 33, "TRIBE WARS IN YOUR BROWSER", NM_SUB)
    if players is not None:
        x = put_text(d, 12, 45, str(players), INK, big)
        put_text(d, x + 5, 50, "PLAYING NOW", MUTED)
    # the word in the middle of the button, sinking with it
    x0, y0, x1, y1 = (v * G / S for v in BUTTON)
    down = hop_at(f)[2]
    x = (x0 + x1 + G / S - big.getlength("PLAY") - 3 * PLAY_SPACING) / 2
    for ch in "PLAY":
        x = put_text(d, x, round((y0 + y1 + G / S) / 2 - 5) + down, ch, NM_INK, big) + PLAY_SPACING
    return im


def nm_palette(scenes: list[Image.Image], text: list[Image.Image]) -> Image.Image:
    """The scene's shades by octree (it keeps the small bright ones, the crown's gold and the
    tribe's blue, that median cut gives up), then every colour of the text and the hopper as it
    is. The count's digits share their colour with the rest of the text, so a new count leaves the
    palette as it was and changes the GIF only where it is written."""
    keep = sorted({c[:3] for im in text for _, c in im.getcolors(1 << 16) if c[3] == 255})
    mosaic = Image.new("RGB", (NM_W, NM_H * len(scenes)))
    for i, im in enumerate(scenes):
        mosaic.paste(im, (0, i * NM_H))
    shades = mosaic.quantize(256 - len(keep), method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE)
    flat = shades.getpalette()[: 3 * (256 - len(keep))] + [v for c in keep for v in c]
    pal = Image.new("P", (1, 1))
    pal.putpalette(flat)
    return pal


def nomads_card(stats: dict) -> tuple[list[Image.Image], Image.Image]:
    """The card's frames and their palette."""
    players = stats.get("nomads", {}).get("players")
    sheet = Sheet()
    ground, masks = nm_ground(), nm_masks()
    frames, scenes, over = [], [], []
    for f in range(NM_FRAMES):
        im = nm_light(nm_scene(sheet, ground, f), masks, f)
        nm_blood(im, f)
        nm_embers(im, f)
        nm_fireflies(im, f)
        nm_play(sheet, im, f)
        text, hopper = nm_text(f, players), nm_hopper(sheet, f)
        big = im.resize((W * S, 120 * S), Image.NEAREST).convert("RGBA")
        for layer in (text, hopper):
            big.alpha_composite(layer.resize((W * S, 120 * S), Image.NEAREST))
        frames.append(big.convert("RGB"))
        scenes.append(im)
        over += [text, hopper]
    return frames, nm_palette(scenes[::8], over)


# ---------------------------------------------------------------- Head Lab

# Each face of the head as the editor's net lays it out (where its 8x8 sits in
# src/headlab-head.png) and in 3D: the corner its top-left texel touches, and the
# edges along its columns (u) and rows (v), on a cube from -1 to 1.
FACES = {
    "front": ((8, 8), (-1, 1, 1), (2, 0, 0), (0, -2, 0)),
    "right": ((0, 8), (-1, 1, -1), (0, 0, 2), (0, -2, 0)),
    "left": ((16, 8), (1, 1, 1), (0, 0, -2), (0, -2, 0)),
    "back": ((24, 8), (1, 1, -1), (-2, 0, 0), (0, -2, 0)),
    "top": ((8, 0), (-1, 1, -1), (2, 0, 0), (0, 0, 2)),
    "bottom": ((8, 16), (-1, -1, 1), (2, 0, 0), (0, 0, -2)),
}
PAINT_ORDER = ["front", "right", "left", "top", "back", "bottom"]
HL_FRAMES = 120  # the whole loop
HL_PAINT = 72  # frames spent painting; the rest the finished head just turns
HL_TURN = 60  # frames per full turn: two turns a loop, so it wraps seamlessly
HL_SCENE_H = 124
NET_X, NET_Y, CELL = 12, 40, 3
MID_X = 122  # the column between the net and the head
EMPTY = ((62, 62, 76), (54, 54, 66))  # the editor's checker under unpainted texels
LIGHT = (-0.35, 0.75, 0.56)


def turn(p, spin: float, tilt: float):
    x, y, z = p
    x, z = x * math.cos(spin) + z * math.sin(spin), -x * math.sin(spin) + z * math.cos(spin)
    y, z = y * math.cos(tilt) - z * math.sin(tilt), y * math.sin(tilt) + z * math.cos(tilt)
    return x, y, z


def draw_head(d: ImageDraw.ImageDraw, tex, painted: set, spin: float, cx: float, cy: float, k: float) -> None:
    tilt, dist = 0.42, 7.0
    lx, ly, lz = (c / math.dist(LIGHT, (0, 0, 0)) for c in LIGHT)
    visible = []
    for name, ((tx, ty), o, u, v) in FACES.items():
        n = turn(tuple(o[a] + u[a] / 2 + v[a] / 2 for a in range(3)), spin, tilt)
        if n[2] > 0.01:
            visible.append((n[2], name, tx, ty, o, u, v, n))
    for _, name, tx, ty, o, u, v, n in sorted(visible):
        shade = round((0.5 + 0.5 * max(0.0, n[0] * lx + n[1] * ly + n[2] * lz)) * 16) / 16
        grid = []
        for j in range(9):
            row = []
            for i in range(9):
                p = turn(tuple(o[a] + u[a] * i / 8 + v[a] * j / 8 for a in range(3)), spin, tilt)
                s = k * dist / (dist - p[2])
                row.append((cx + p[0] * s, cy - p[1] * s))
            grid.append(row)
        for j in range(8):
            for i in range(8):
                c = tex.getpixel((tx + i, ty + j)) if (name, i, j) in painted else EMPTY[(i + j) % 2]
                c = tuple(int(ch * shade) for ch in c)
                quad = [grid[j][i], grid[j][i + 1], grid[j + 1][i + 1], grid[j + 1][i]]
                d.polygon(quad, fill=c, outline=c)


def headlab_card(stats: dict) -> list[Image.Image]:
    st = stats["headlab"]
    tex = Image.open(SRC / "headlab-head.png").convert("RGB")
    order = [(name, i, j) for name in PAINT_ORDER for j in range(8) for i in range(8)]
    swatches = sorted(
        {c for _, c in tex.quantize(16, method=Image.Quantize.MEDIANCUT).convert("RGB").getcolors()},
        key=lambda c: sum(c),
    )
    frames = []
    for f in range(HL_FRAMES):
        n = min(len(order), (f + 1) * len(order) // HL_PAINT)
        painted = set(order[:n])
        im, d = scene(HL_SCENE_H)

        x = put_text(d, 12, 10, "HEAD ", INK, PX_BIG)
        put_text(d, x, 10, "LAB", GREEN, PX_BIG)
        put_text(d, 12, 26, "PAINT PLAYER HEADS IN-GAME · FABRIC", MUTED)
        # K's ink is 4px wide, one pixel in from the pen: 8px inside the keycap leaves 2px either side
        d.rectangle([232, 8, 241, 16], fill=(40, 40, 56), outline=INK)
        put_text(d, 234, 10, "K", INK)
        put_text(d, 246, 10, "OPEN EDITOR", MUTED)

        # the editor's net, filling in texel by texel, and its palette with the colour in hand
        for name, ((tx, ty), *_rest) in FACES.items():
            for j in range(8):
                for i in range(8):
                    c = tex.getpixel((tx + i, ty + j)) if (name, i, j) in painted else EMPTY[(i + j) % 2]
                    x0, y0 = NET_X + (tx + i) * CELL, NET_Y + (ty + j) * CELL
                    d.rectangle([x0, y0, x0 + CELL - 1, y0 + CELL - 1], fill=c)
        brush = None
        if f < HL_PAINT:
            name, i, j = order[n - 1]
            (tx, ty), *_rest = FACES[name]
            brush = tex.getpixel((tx + i, ty + j))
            x0, y0 = NET_X + (tx + i) * CELL, NET_Y + (ty + j) * CELL
            d.rectangle([x0 - 1, y0 - 1, x0 + CELL, y0 + CELL], outline=INK)
        sy = NET_Y + 3 * 8 * CELL + 4
        for s, c in enumerate(swatches):
            x0 = NET_X + s * 5
            d.rectangle([x0, sy, x0 + 2, sy + 2], fill=c)
        if brush:
            near = min(range(len(swatches)), key=lambda s: math.dist(swatches[s], brush))
            d.rectangle([NET_X + near * 5 - 1, sy - 1, NET_X + near * 5 + 3, sy + 3], outline=INK)

        # middle: the mod's numbers as the tooltip of the item it makes, and progress under it
        bottom = tooltip(d, MID_X, NET_Y, [
            (None, [("HEAD LAB", MC_YELLOW)]),
            ("down", [(compact(st["modrinth"] + st["curseforge"]), MC_WHITE), (" DOWNLOADS", MC_GREY)]),
            ("", [(compact(st["modrinth"]), MC_WHITE), (" MODRINTH", GREEN)]),
            ("", [(compact(st["curseforge"]), MC_WHITE), (" CURSEFORGE", CURSEFORGE)]),
            ("heart", [(str(st["followers"]), MC_WHITE), (" FOLLOWERS", MC_GREY)]),
            ("grass", [("-".join(st["mc"]), MC_WHITE)]),
        ])
        if f < HL_PAINT:
            x = put_text(d, MID_X, bottom + 6, "PAINTING ", MUTED)
            put_text(d, x, bottom + 6, f"{n}/{len(order)}", INK)
        else:
            put_icon(d, "check", MID_X, bottom + 5, GREEN)
            put_text(d, MID_X + 9, bottom + 6, "HEAD GENERATED", GREEN)

        big = im.resize((W * S, HL_SCENE_H * S), Image.NEAREST)
        bd = ImageDraw.Draw(big)
        spin = 2 * math.pi * f / HL_TURN
        cx, cy, k = 268 * S, 68 * S + 3 * math.sin(spin), 25 * S
        bd.ellipse([cx - 1.1 * k, 116 * S - 10, cx + 1.1 * k, 116 * S + 6], fill=(12, 12, 18))
        draw_head(bd, tex, painted, spin, cx, cy, k)
        frames.append(big)
    return frames


# ---------------------------------------------------------------- claude-fleet

FL_MS = 80
FL_FRAMES = 140  # a multiple of Clawd's dance (8 beats of 200 ms = 20 frames) and of the spinner
FL_SCENE_H = 120  # base pixels: 360 in the GIF
MONO = str(FONTS / "JetBrainsMono.ttf")
PROMPT = "fix the failing test in auth.spec.ts"
SUBMIT = 4 + len(PROMPT) + 2  # typed one character a frame after a short pause
DONE = 104
SPIN = [0, 1, 2, 3, 4, 5, 4, 3, 2, 1]

# claude-fleet's default theme (src/config.rs)
ACCENT = (217, 119, 87)
TEXT = (230, 225, 218)
FL_MUTED = (122, 116, 110)
FAINT = (74, 70, 66)
BUSY = (224, 176, 76)
IDLE = (110, 168, 122)
DEAD = (176, 90, 90)
SURFACE = (26, 24, 23)
SELECT = (46, 40, 37)
CHROME = (38, 35, 33)

# Clawd's dance as claude-fleet's src/clawd.rs puts it together: a 12x4 body
# with two holes for eyes, two-pixel arms, feet in pairs, eight poses a beat apart.
CLAWD_W, CLAWD_H, BODY_W, BODY_H, STEP, HOME = 22, 7, 12, 4, 2, 5
CLAWD_BEAT_MS = 200
EYES, FEET = (2, 9), ((1, 3), (8, 10))
ARMS = {"out": ((-2, 2), (-1, 2)), "up": ((-2, 0), (-1, 1)), "down": ((-1, 3), (-2, 4))}
DANCE = [  # body's left edge, hop, left arm, right arm, which feet are down (left pair, right pair), look
    (HOME, False, "out", "out", (True, True), 0),
    (HOME, True, "out", "out", (True, True), 0),
    (HOME, False, "out", "out", (True, True), 0),
    (HOME, True, "up", "up", (True, True), 0),
    (HOME - STEP, False, "up", "down", (True, False), -1),
    (HOME, False, "out", "out", (True, True), -1),
    (HOME + STEP, False, "down", "up", (False, True), 1),
    (HOME, False, "out", "out", (True, True), 1),
]


def clawd_pixels(beat: int) -> set[tuple[int, int]]:
    x, hop, left, right, feet, look = DANCE[beat % len(DANCE)]
    top = 0 if hop else STEP  # feet stand on the bottom row; a hop lifts the body a step off it
    px = {
        (x + col, top + row)
        for row in range(BODY_H)
        for col in range(BODY_W)
        if not (row == 1 and any(e + look == col for e in EYES))
    }
    px.update((x + dx, top + dy) for dx, dy in ARMS[left])
    px.update((x + BODY_W - 1 - dx, top + dy) for dx, dy in ARMS[right])
    base = x if hop else HOME  # feet on the ground stay put while the body sways over them
    for pair, down in zip(FEET, feet):
        if down:
            px.update((base + dx, top + BODY_H) for dx in pair)
    return {(a, b) for a, b in px if 0 <= a < CLAWD_W and 0 <= b < CLAWD_H}


def mono(size: int, weight: str):
    font = ImageFont.truetype(MONO, size)
    font.set_variation_by_name(weight)
    return font


class Term:
    """A character grid on the card: text and the few glyphs JetBrains Mono lacks, drawn by hand."""

    def __init__(self, d: ImageDraw.ImageDraw, x0: int, y0: int):
        self.d, self.x0, self.y0 = d, x0, y0
        self.font, self.bold = mono(15, b"Regular"), mono(15, b"Bold")
        self.cw, self.lh = self.font.getlength("M"), 20
        asc, desc = self.font.getmetrics()
        self.dy = (self.lh - asc - desc) // 2 + 1

    def xy(self, col: float, row: float):
        return self.x0 + col * self.cw, self.y0 + row * self.lh

    def text(self, col: float, row: int, s: str, fill, bold=False) -> float:
        x, y = self.xy(col, row)
        self.d.text((x, y + self.dy), s, fill=fill, font=self.bold if bold else self.font)
        return col + len(s)

    def spans(self, col: float, row: int, parts) -> None:
        for s, fill in parts:
            col = self.text(col, row, s, fill)

    def center(self, col: float, row: int):
        x, y = self.xy(col, row)
        return x + self.cw / 2, y + self.lh / 2

    def dot(self, col: float, row: int, fill) -> None:  # Claude Code's ⏺
        x, y = self.center(col, row)
        self.d.ellipse([x - 4, y - 4, x + 4, y + 4], fill=fill)

    def elbow(self, col: float, row: int) -> None:  # ⎿
        x, y = self.center(col, row)
        self.d.line([(x - 2, y - 9), (x - 2, y + 1), (x + 5, y + 1)], fill=FL_MUTED, width=1)

    def clawd(self, col: int, row: int, beat: int) -> None:
        """Clawd at the fleet's scale 2, a pixel to a cell, with its top left corner in cell (col, row)."""
        x0, y0 = self.xy(col, row)
        for x, y in clawd_pixels(beat):
            self.d.rectangle(
                [round(x0 + x * self.cw), y0 + y * self.lh, round(x0 + (x + 1) * self.cw) - 1, y0 + (y + 1) * self.lh - 1],
                fill=ACCENT,
            )

    def spinner(self, col: float, row: int, f: int, fill=ACCENT) -> None:  # · ✢ ✳ ✶ ✻ ✽, as arms that grow and shrink
        x, y = self.center(col, row)
        r = SPIN[f % len(SPIN)]
        if r == 0:
            self.d.rectangle([x - 1, y - 1, x + 1, y + 1], fill=fill)
            return
        for a in range(3 if r < 3 else 4):
            t = math.pi * a / (3 if r < 3 else 4) + math.pi / 2
            dx, dy = math.cos(t) * (r + 1), math.sin(t) * (r + 1)
            self.d.line([(x - dx, y - dy), (x + dx, y + dy)], fill=fill, width=2)

    def star(self, col: float, row: int, fill) -> None:  # ★
        x, y = self.center(col, row)
        pts = []
        for i in range(10):
            r, a = (6.5 if i % 2 == 0 else 2.8), -math.pi / 2 + i * math.pi / 5
            pts.append((x + r * math.cos(a), y + 1 + r * math.sin(a)))
        self.d.polygon(pts, fill=fill)

    def hline(self, row: int, c0: int, c1: int) -> None:
        (x0, y), (x1, _) = self.center(c0, row), self.center(c1, row)
        self.d.line([(x0, y), (x1, y)], fill=FAINT)

    def vline(self, col: int, r0: int, r1: int) -> None:
        (x, y0), (_, y1) = self.center(col, r0), self.center(col, r1)
        self.d.line([(x, y0), (x, y1)], fill=FAINT)


def fleet_frame(t: Term, f: int, cols: int, st: dict) -> None:
    right = cols - 1
    for row in (0, 11, 13):
        t.hline(row, 0, right)
    t.vline(0, 0, 13)
    t.vline(right, 0, 13)
    t.vline(22, 0, 11)
    for col, title in ((2, " SESSIONS "), (24, " api-a6 ")):
        x, y = t.xy(col, 0)
        t.d.rectangle([x, y + 4, x + len(title) * t.cw, y + t.lh - 4], fill=SURFACE)
        t.text(col, 0, title, TEXT, bold=True)

    # Clawd dances in the pane's bottom right corner the whole time, as it does in the fleet
    t.clawd(right - 1 - CLAWD_W, 11 - CLAWD_H, f * FL_MS // CLAWD_BEAT_MS)

    working = SUBMIT <= f < DONE
    # session list
    x0, y0 = t.xy(1, 3)
    x1, _ = t.xy(21, 3)
    t.d.rectangle([x0 + 4, y0, x1 + t.cw - 4, y0 + 2 * t.lh - 1], fill=SELECT)
    t.dot(2, 1, IDLE)
    t.text(4, 1, "web-c3", TEXT)
    t.text(4, 2, "~/web", FL_MUTED)
    if working:
        t.spinner(2, 3, f, BUSY)
    else:
        t.dot(2, 3, IDLE if f >= DONE else FAINT)
    t.text(4, 3, "api-a6", TEXT, bold=True)
    t.text(12, 3, "◀", ACCENT)
    t.text(4, 4, "apps/api", FL_MUTED)
    t.spinner(2, 5, f + 3, BUSY)
    t.text(4, 5, "docs-f1", TEXT)
    t.text(4, 6, "apps/docs", FL_MUTED)
    t.text(2, 8, "── UNREACHABLE", FAINT)
    t.text(2, 9, "!", DEAD, bold=True)
    t.text(4, 9, "dashboard…", FL_MUTED)

    # the conversation in the focused session
    typed = PROMPT[: max(0, min(len(PROMPT), f - 4))]
    t.spans(24, 1, [("> ", FL_MUTED), (typed, TEXT)])
    if f < SUBMIT and (f // 4) % 2 == 0:
        x, y = t.xy(26 + len(typed), 1)
        t.d.rectangle([x + 1, y + 3, x + t.cw - 1, y + t.lh - 3], fill=TEXT)
    steps = [
        (SUBMIT + 10, "Read", "src/auth.spec.ts", [("Read ", FL_MUTED), ("120", TEXT), (" lines", FL_MUTED)]),
        (SUBMIT + 22, "Update", "src/auth.spec.ts", [("Updated with ", FL_MUTED), ("2", GREEN), (" additions and ", FL_MUTED), ("1", RED), (" removal", FL_MUTED)]),
        (SUBMIT + 34, "Bash", "cargo test", [("Running…", FL_MUTED)]),
    ]
    row = 3
    for start, tool, arg, result in steps:
        if f < start:
            break
        t.dot(24, row, GREEN)
        t.spans(26, row, [(tool, TEXT), (f"({arg})", FL_MUTED)])
        if tool == "Bash" and f >= SUBMIT + 50:
            result = [("test result: ", FL_MUTED), ("ok", GREEN), (". 42 passed", FL_MUTED)]
        t.elbow(26, row + 1)
        t.spans(28, row + 1, result)
        row += 2
    if f >= DONE:
        t.dot(24, 9, TEXT)
        t.text(26, 9, "Fixed: the mock clock ran a tick late.", TEXT)
    elif working:
        secs = 1 + (f - SUBMIT) * FL_MS // 1000
        tokens = 0.3 + (f - SUBMIT) * 0.02
        verb = "Testing…" if f >= SUBMIT + 34 else "Thinking…"
        t.spinner(24, 10, f)
        t.spans(26, 10, [(verb, ACCENT), (f" ({secs}s · ↑ {tokens:.1f}k tokens)", FL_MUTED)])

    # the status line carries the repo's numbers, centred
    gap = ("   ", FL_MUTED)
    numbers = [
        (str(st["stars"]), TEXT), (" star" + "s" * (st["stars"] != 1), FL_MUTED), gap,
        (str(st["forks"]), TEXT), (" fork" + "s" * (st["forks"] != 1), FL_MUTED), gap,
        ("↓ ", ACCENT), (compact(st["downloads"]), TEXT), (" downloads", FL_MUTED), gap,
        (str(st["releases"]), TEXT), (" releases", FL_MUTED),
    ]
    col = (cols - (2 + sum(len(s) for s, _ in numbers))) // 2
    t.star(col, 12, BUSY)
    t.spans(col + 2, 12, numbers)


def fleet_card(stats: dict) -> list[Image.Image]:
    st = stats["fleet"]
    base, _ = scene(FL_SCENE_H)
    backdrop = base.resize((W * S, FL_SCENE_H * S), Image.NEAREST)
    wx0, wy0, wx1, wy1 = 16, 14, W * S - 16, FL_SCENE_H * S - 14
    title = mono(13, b"Regular")
    frames = []
    for f in range(FL_FRAMES):
        im = backdrop.copy()
        d = ImageDraw.Draw(im)
        d.rounded_rectangle([wx0, wy0, wx1, wy1], radius=8, fill=SURFACE, outline=FAINT)
        d.rounded_rectangle([wx0, wy0, wx1, wy0 + 26], radius=8, fill=CHROME)
        d.rectangle([wx0 + 1, wy0 + 18, wx1 - 1, wy0 + 26], fill=CHROME)
        for i, c in enumerate(((255, 95, 87), (254, 188, 46), (40, 200, 64))):
            d.ellipse([wx0 + 12 + i * 18, wy0 + 8, wx0 + 22 + i * 18, wy0 + 18], fill=c)
        label = f"claude-fleet {st['latest']}".strip()
        d.text(((wx0 + wx1 - title.getlength(label)) / 2, wy0 + 5), label, fill=FL_MUTED, font=title)
        t = Term(d, wx0 + 14, wy0 + 26 + 12)
        cols = int((wx1 - wx0 - 28) // t.cw)
        fleet_frame(t, f, cols, st)
        frames.append(im)
    return frames


def main() -> None:
    only = "nomads" if "--nomads" in sys.argv else None
    stats = load_stats("--offline" in sys.argv, only)
    ASSETS.mkdir(exist_ok=True)
    frames, palette = nomads_card(stats)
    save_gif(frames, ASSETS / "nomads.gif", NM_MS, palette)
    if only:
        return
    save_gif(headlab_card(stats), ASSETS / "headlab.gif", 70)
    save_gif(fleet_card(stats), ASSETS / "claude-fleet.gif", FL_MS)


if __name__ == "__main__":
    main()
