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
from collections import namedtuple
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
# its table (nomads-sprites.json: the sprites, and the flame's loop) are written by
# the game's scripts/profile-sprites.ts (`npm run profile-sprites` in the game, then
# copy dist/profile/* here, when its art changes). The flame is the campfire's
# automaton through the loop; the light is the game's night (render/light.ts) and its
# campfire glow (render/world/fire.ts); what happens is the game's own, timed as it
# times it (render/world/draw/kin.ts, tree.ts, items.ts; render/fx.ts).
# the loop: as long as the flame's, in frames as long as its
NM_FLAME = json.loads((SRC / "nomads-sprites.json").read_text())["flame"]
NM_FRAMES, NM_MS = NM_FLAME["frames"], NM_FLAME["ms"]
G = 3  # the scene's game pixels to a GIF pixel, as the text's
NM_W, NM_H = W * S // G, 120 * S // G  # 320 x 120 game pixels
FIRE = (232, 70)  # the campfire's foot
MEADOW = [(0x6A, 0x9D, 0x47), (0x5B, 0x8F, 0x3D), (0x4D, 0x7C, 0x34), (0x3F, 0x6A, 0x2B)]
NIGHT_FOG = (56, 64, 106)  # the game's night sky dimmed, a little less than its fog of war
LAMPLIGHT = (240, 206, 178)  # what the tribe sees by at night: the night sky lifted towards the fire's warmth
POOL = (78, 38)  # how far the fire lights the ground either way, flattened as the ground is seen
STOKED_LIGHT = 1.25  # a stoked fire's light reaches this much further (render/world/draw/items.ts)
GLOW_TINT, GLOW = (255, 138, 42), 0.42
KING_IDLE = 4  # the king's horse breathing: its idle frames, as exported
# a spark cools through the fire's own colours (art/sprites/items.ts FIRE_RAMP)
EMBER = [(0xFF, 0xFB, 0xE8), (0xFF, 0xF0, 0x7A), (0xFF, 0xC4, 0x3C), (0xF7, 0xA5, 0x31), (0xD9, 0x50, 0x2F), (0x9C, 0x2C, 0x1F)]
OUTLINE, HEAL, MEAT_LIGHT = (0x1E, 0x1A, 0x24), (0xE0, 0x54, 0x6A), (0xE0, 0x6A, 0x5A)  # art/palette.ts C

# the menu's look (client/index.html): gold title, green PLAY with a darker lip under it
GOLD, NM_INK, NM_SUB = (242, 201, 76), (30, 26, 36), (200, 192, 176)
PLAY_UP, PLAY_DOWN, PLAY_LIP = (116, 184, 58), (134, 201, 74), (70, 117, 34)
PLAY_SPACING = 2  # px between its letters, as the menu spaces them
# the ring sitting round the back of the fire, open to the viewer: which kin (as exported),
# where, which way it faces (0 south .. 4 north, the game's), mirrored
CIRCLE = [(4, 218, 61, 1, False), (3, 246, 60, 1, True)]
KING_AT, KING_DIR = (266, 78), 2  # the king on his horse, side on, facing west (east mirrored): to the fire
TREES = [  # name, x, y: a dark wood round the glade, thickest behind the camp
    ("tree_1_3_1", 316, 44), ("tree_0_2_1", 200, 34), ("tree_2_2_3", 232, 26), ("tree_2_2_1", 176, 28),
    ("tree_1_2_2", 270, 32), ("tree_0_2_2", 136, 22), ("tree_1_2_0", 104, 16), ("tree_0_1_2", 70, 18),
    ("tree_1_1_1", 306, 104), ("tree_2_2_0", 300, 70),
]
TUFTS = [("grass_tall_1_0", 196, 92), ("grass_tall_0_2", 254, 98), ("grass_tall_0_1", 226, 106), ("grass_short_0_1", 248, 88),
         ("grass_short_0_0", 200, 52), ("grass_short_0_2", 286, 60), ("grass_tall_0_0", 136, 98), ("grass_tall_1_1", 182, 112)]
FIREFLIES = [(112, 20, 0), (124, 64, 1), (300, 14, 2), (100, 42, 3), (296, 90, 4)]  # where each drifts about
# the game's sprite directions by octant from east, clockwise (art/figures/frames.ts OCTANT): (dir, mirrored)
OCTANT = [(2, False), (1, False), (0, False), (1, True), (2, True), (3, True), (4, False), (3, False)]
WALK_STEP = 0.12  # s a walking frame (render/world/draw/kin.ts)
SIGHT, SIGHT_UP = (50, 44), 12  # how far a kin's sight lifts the night round him (render/light.ts: a disc, as round as the game's), from over his feet

# What happens, in frames of the loop (the flame's: nomads-sprites.json says how many, how long,
# and when the woodcutter's log lands on the fire, STOKED):
#   The woodcutter (kin 0) chops the cherry, a swing every CHOP_EVERY; at the second blow an
#   apple drops onto the head of the man under it (kin 6); the fifth fells it (north-east, towards
#   the camp and away from the man: render/world/draw/tree.ts falls it in FALL_TIME), and its wood he takes up,
#   carries to the fire and throws on (ANIM.TOSS, the log's flight render/fx-tools.ts EV.STOKE): it flares up,
#   STOKED floats up, and the flame grows, lazier, and burns down again by the loop's end.
#   The archer (kin 1) gets up from the fire, walks out, draws as a bow is drawn (frames.ts
#   ACTIONS.draw: 0, 0.2, 0.5 s, loosed at BOW_DRAW_TIME 0.8 s) at the apple on the man's head
#   and hits the man; he goes back to his seat.
#   The man, hit, flashes and is knocked back off his feet, the apple flying off his head: over onto
#   his back, legs kicking under the stars, WHOOPS! (draw/kin.ts ANIM.TRIP, fx-tools.ts EV.TRIP),
#   up again with a hop. He takes up his apple, shows his HP bar (draw/kin.ts healthBar), walks to
#   the fire, sits, eats the apple and heals by it (render/fx.ts EV.HEAL's heart), the bar filling,
#   gone once he is whole; then back under his tree, which has grown again as a stump puts out a
#   shoot in the game.
TREE_AT, TREE = (168, 77), "tree_3_2_0"  # the cherry, grown to its second size: it reads by night
STUMP, LOGS = "stump_3_2", ["log_3_2_0", "log_3_2_1"]  # its stump, and its logs lying, + the way it fell
FRUIT = (-9, -15)  # where its fruit hangs on its left side, from its foot
BLOSSOM = (236, 150, 186)  # its leaves flying as it falls
CHOP_AT, MAN_AT, MAN_SEAT = (179, 79), (155, 82), (215, 84)  # where the woodcutter chops, the man stands and sits
SEAT, SHOT = (208, 78), (186, 93)  # the archer's seat by the fire, and where he shoots from
TOSS_AT = (204, 64)  # where the woodcutter throws his log from
CHOP_FIRST, CHOP_EVERY, BLOWS = 2, 12, 5  # his first swing, a swing every so many frames, the blow that fells it
APPLE_BLOW = 1  # the blow that shakes the apple down (counting from 0)
CHOP = [(0, "chop0"), (0.11, "chop1"), (0.2, "chop2"), (0.32, "chop3"), (0.5, "idle0")]  # ACTIONS.chop, ACTION_END
FALL_TIME, UPRIGHT, GROUND_Y = 0.7, 0.81, 0.58  # draw/tree.ts; art/props/trees.ts
FALL_DIR = 7  # north-east, towards the camp: 0 east, clockwise in eighths
LOG_ALONG = [14, 36]  # each log from the stump along the fall: LOG_GAP + logStep(2) * (i + 1/2)
TOSS = [(0, "bend"), (0.175, "raise0"), (0.3, "raise1"), (0.5, "idle0")]  # ANIM.TOSS over TOSS_TIME
LOG_FLIGHT, HAND_Z, LOG_SPIN, TOSS_FALL = 0.4, 16, 0.75, 260  # the thrown log: s in the air, from this high, turns, its fall
# the archer: stands, walks out, draws, looses; walks back, sits
UP, WALK_OUT, DRAWS, WALK_BACK, SITS = 24, 26, 37, 55, 66
DRAW_FRAMES = [(0, "draw0"), (0.2, "draw1"), (0.5, "draw2"), (0.8, "idle0")]
BOW_HAND = (-6, -8)  # where the arrow leaves him, aiming west, from his feet
ARROW_FLIGHT = 3  # frames
# the man: hit and knocked off his feet, up again, takes up his apple, walks to the fire, sits,
# eats it, heals, gets up, walks back
MAN_HP, MAN_HIT_HP = 3, 1
KNOCK, KNOCK_TIME = 6, 0.25  # px the arrow knocks him back, away from the archer, and the s he slides
HURT_FOR, HURT_TINT = 0.15, (0xFF, 0x70, 0x70)  # the hit's flash (draw/kin.ts HURT_TINT)
DOWN_FOR = 1.6  # s off his feet, getting up included (ANIM.TRIP)
TRIP_FALL, TRIP_RISE, KICK_STEP = 0.16, 0.3, 0.09  # draw/kin.ts: going over, getting up, a kick of his legs
LYING_STARS = [(0, -15), (-4, -14), (-5, -12), (-4, -10), (0, -9)]  # draw/kin.ts, by sprite direction
DUST, GRASS, WHOOPS = (0x69, 0x62, 0x5B), (0x8F, 0xC7, 0x64), (0xFF, 0xF0, 0xA0)  # C.groundLight, C.grassLight, C.goldLight
APPLE_FLIGHT, APPLE_LANDS = 0.5, (-4, 7)  # the apple knocked off his head: s in the air, where it lands from where he does
MAN_WALKS, MAN_SITS, HEARTS, MAN_UP, MAN_BACK = 74, 94, (100, 114), 124, 146
APPLE_ON = {"stand": 12, "sit": 8}  # how high over his feet the apple sits on his head, standing and sitting
APPLE_HELD = 2  # and its foot in his hands, held before him

# Under the title how many play now, and lower down PLAY with a kinsman hopping on it as the
# tribesmen on the game's loading curtain do (lobby/loading-row.ts and its hop-* keyframes):
# standing, crouched with his hands down, up with his arms up 11 px, landing in the crouch. He
# stands in the middle of it with a faint shadow under him; each landing presses it and each jump
# lets it up; he is drawn over the word on it. The curtain's 0.8 s is ten frames.
HOPPER = 5
HOP_POSE = [(0, "idle0"), (0.12, "bend"), (0.22, "raise1"), (0.62, "bend"), (0.72, "idle0")]
HOP_LIFT = [(0, 0), (0.24, 4), (0.29, 8), (0.35, 10), (0.43, 11), (0.51, 10), (0.56, 8), (0.60, 4), (0.62, 0)]
HOP_TIME = 0.8
BUTTON = (12, 93, 82, 111)  # game px: PLAY, its lip a pixel under it
STAND_ON = 102  # the row of it his feet are on: its middle


def noise(x: int, y: int, seed: int) -> float:
    """The same speckle every run: a hash of the pixel, 0..1."""
    h = (x * 374761393 + y * 668265263 + seed * 2246822519) & 0xFFFFFFFF
    h = ((h ^ (h >> 13)) * 1274126177) & 0xFFFFFFFF
    return (h ^ (h >> 16)) / 0xFFFFFFFF


class Sheet:
    """The exported sprites by name, each stamped with its anchor on the spot asked for, and the flame's loop."""

    def __init__(self):
        self.im = Image.open(SRC / "nomads-sprites.png").convert("RGBA")
        table = json.loads((SRC / "nomads-sprites.json").read_text())
        self.frames, self.flame = table["sprites"], table["flame"]
        self.cache = {}

    def get(self, name: str, flip: bool = False, tint=None):
        if (name, flip, tint) not in self.cache:
            if (name, False, None) in self.cache:  # drawn here rather than exported
                im, ax, ay = self.cache[name, False, None]
                w = im.width
            else:
                f = self.frames[name]
                im = self.im.crop((f["x"], f["y"], f["x"] + f["w"], f["y"] + f["h"]))
                ax, ay, w = f["ax"], f["ay"], f["w"]
            im = im.transpose(Image.FLIP_LEFT_RIGHT) if flip else im
            if tint:  # multiplied, as the game tints a sprite
                im = ImageChops.multiply(im, Image.new("RGBA", im.size, (*tint, 255)))
            self.cache[name, flip, tint] = (im, w - ax if flip else ax, ay)
        return self.cache[name, flip, tint]

    def turned(self, name: str, degrees: float, stretch: float = 1.0) -> str:
        """`name` stretched upright by `stretch` and turned `degrees` clockwise about its anchor (as a
        sprite's scale and rotation in the game), kept as a sprite of its own."""
        key = f"{name}@{round(degrees)}x{round(stretch, 2)}"
        if (key, False, None) not in self.cache:
            im, ax, ay = self.get(name)
            if stretch != 1.0:
                h = max(1, round(im.height * stretch))
                im, ay = im.resize((im.width, h), Image.NEAREST), ay * h / im.height
            side = 2 * max(im.width, im.height) + 2
            big = Image.new("RGBA", (side, side))
            cx = cy = side // 2
            big.alpha_composite(im, (round(cx - ax), round(cy - ay)))
            big = big.rotate(-round(degrees), Image.NEAREST, center=(cx, cy))
            box = big.getbbox() or (cx, cy, cx + 1, cy + 1)
            self.cache[key, False, None] = (big.crop(box), cx - box[0], cy - box[1])
        return key

    def put(self, dst: Image.Image, name: str, x: float, y: float, flip: bool = False, tint=None) -> None:
        im, ax, ay = self.get(name, flip, tint)
        x0, y0 = round(x - ax), round(y - ay)
        # alpha_composite takes no negative destination: cut off what hangs over the top or left
        cx, cy = max(0, -x0), max(0, -y0)
        if cx < im.width and cy < im.height:
            dst.alpha_composite(im, (x0 + cx, y0 + cy), (cx, cy))

    def text(self, dst: Image.Image, s: str, x: float, y: float, colour) -> None:
        """`s` in the game's pixel font, centred on x, its top at y (render/fx.ts floatText, art/font.ts)."""
        x0 = round(x - (len(s) * 4 - 1) // 2)
        for i, ch in enumerate(s):
            if ch != " ":
                self.put(dst, f"g_{ch}", x0 + i * 4, y, tint=colour)


def smoothstep(a: float, b: float, x: float) -> float:
    k = min(1.0, max(0.0, (x - a) / (b - a)))
    return k * k * (3 - 2 * k)


def stepped(keys: list, t: float):
    """What the keyframes say at `t`, held from one to the next as `steps(1, end)` holds them."""
    return [v for at, v in keys if at <= t][-1]


def facing(dx: float, dy: float):
    """The sprite direction and mirroring for a heading, as the game picks them (spriteDir)."""
    return OCTANT[round(math.atan2(dy, dx) / (math.pi / 4)) % 8]


def secs(frames: float) -> float:
    return frames * NM_MS / 1000


def frames_of(seconds: float) -> int:
    """The nearest whole frame to `seconds` in (halves up)."""
    return int(seconds * 1000 / NM_MS + 0.5)


def walking(kin: int, f: int, start: int, end: int, a, b):
    """Kin `kin` walking from `a` to `b` over frames `start` to `end`: (x, y, sprite, mirrored)."""
    u = (f - start) / (end - start)
    d, flip = facing(b[0] - a[0], b[1] - a[1])
    step = int(secs(f - start) / WALK_STEP) % 4
    return a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u, f"kin_{kin}_walk{step}_{d}", flip


def blow_frames() -> list[int]:
    """The frames the woodcutter's axe lands in: 0.2 s into each swing (SWING_WINDUP)."""
    return [CHOP_FIRST + k * CHOP_EVERY + frames_of(0.2) for k in range(BLOWS)]


def tree_state(f: int):
    """The cherry: standing (and how hard the axe shakes it), falling (how far), or felled."""
    blows = blow_frames()
    fell = blows[-1]
    if f < fell:
        since = [f - b for b in blows if 0 <= f - b < 3]
        return "standing", (1 - since[0] / 3) if since else 0.0
    k = secs(f - fell) / FALL_TIME
    return ("falling", k) if k < 1 else ("felled", 0.0)


def log_at(i: int):
    """Where log `i` of the felled cherry lies (0 by the stump), along the way it fell
    (render/world/draw/tree.ts), and what it sorts by: its far edge."""
    a = FALL_DIR * math.pi / 4
    x = TREE_AT[0] + math.cos(a) * LOG_ALONG[i]
    y = TREE_AT[1] + math.sin(a) * LOG_ALONG[i] * GROUND_Y
    return round(x), round(y), round(y) - abs(math.sin(a)) * (LOG_ALONG[1] - LOG_ALONG[0]) / 2 * GROUND_Y - 4


def woodcutter_plan(stoked: int) -> dict:
    """When the woodcutter does what after felling the tree, for the log to land at `stoked`."""
    fell = blow_frames()[-1]
    down = fell + frames_of(FALL_TIME)
    leave = stoked - frames_of(LOG_FLIGHT)  # the log leaves his hands 0.175 s into the throw
    return {"fell": fell, "down": down, "pick": down + 2, "carry": down + 6, "toss": leave - 2, "leave": leave,
            "back": stoked + 3, "home": stoked + 15}


def woodcutter_at(f: int, stoked: int):
    """The woodcutter in frame `f`: (x, y, sprite, mirrored, holding the log)."""
    p = woodcutter_plan(stoked)
    fell, pick, carry, toss, back, home = p["fell"], p["pick"], p["carry"], p["toss"], p["back"], p["home"]
    lx, ly, _ = log_at(0)
    if f < fell + 3:  # at the tree, swinging
        swing = (f - CHOP_FIRST) % CHOP_EVERY if f >= CHOP_FIRST else CHOP_EVERY
        frame = stepped(CHOP, secs(swing)) if swing < CHOP_EVERY else "idle0"
        return *CHOP_AT, f"kin_0_{frame}_2", True, False
    if f < pick:  # watching it fall
        return *CHOP_AT, "kin_0_idle0_2", True, False
    if f < carry:  # over the log, taking it up
        d, flip = facing(lx - CHOP_AT[0], ly - CHOP_AT[1])
        return *CHOP_AT, f"kin_0_bend_{d}", flip, False
    if f < toss:
        return *walking(0, f, carry, toss, CHOP_AT, TOSS_AT), True
    if f < back:  # the throw: down for it, then up over his head and away
        d, flip = facing(FIRE[0] - TOSS_AT[0], FIRE[1] - TOSS_AT[1])
        return *TOSS_AT, f"kin_0_{stepped(TOSS, secs(f - toss))}_{d}", flip, f < p["leave"]
    if f < home:
        return *walking(0, f, back, home, TOSS_AT, CHOP_AT), False
    return *CHOP_AT, "kin_0_idle0_2", True, False


def archer_at(f: int):
    """The archer in frame `f`: (x, y, sprite, mirrored)."""
    if f < UP or f >= SITS:
        return *SEAT, "kin_1_sit_2", False  # by the fire, facing it
    if WALK_OUT <= f < DRAWS:
        return walking(1, f, WALK_OUT, DRAWS, SEAT, SHOT)
    if WALK_BACK <= f < SITS:
        return walking(1, f, WALK_BACK, SITS, SHOT, SEAT)
    x, y = SEAT if f < WALK_OUT else SHOT
    d, flip = facing(MAN_AT[0] - x, MAN_AT[1] - y)
    frame = stepped(DRAW_FRAMES, secs(f - DRAWS)) if DRAWS <= f else "idle0"
    return x, y, f"kin_1_{frame}_{d}", flip


def looses() -> int:
    return DRAWS + frames_of(0.8)


Man = namedtuple("Man", "x y name flip hp lift tint lying")


def man_hit() -> int:
    """The frame the arrow strikes the man."""
    return looses() + ARROW_FLIGHT


def knocked():
    """Where the arrow knocks the man (back from the archer, the way draw/kin.ts knocks a figure
    back from whoever it faces), and where the apple it knocks off his head lands: before him."""
    dx, dy = MAN_AT[0] - SHOT[0], MAN_AT[1] - SHOT[1]
    n = math.hypot(dx, dy)
    x, y = MAN_AT[0] + dx / n * KNOCK, MAN_AT[1] + dy / n * 0.6 * KNOCK
    return (x, y), (x + APPLE_LANDS[0], y + APPLE_LANDS[1])


def man_at(f: int) -> Man:
    """The man under the tree in frame `f`."""
    hit, (dx, dy) = man_hit(), knocked()[0]
    up = hit + frames_of(DOWN_FOR)
    hp = MAN_HP if f < hit else min(MAN_HP, MAN_HIT_HP + sum(1 for h in HEARTS if f >= h))
    breath = "idle0" if (f // 8) % 2 == 0 else "idle1"
    if f < hit or f >= MAN_BACK:
        return Man(*MAN_AT, f"kin_6_{breath}_0", False, hp, 0, None, False)
    if f < up:
        # off his feet, facing the archer: sliding back as he goes over backwards, on his back with
        # his legs kicking, and up again (draw/kin.ts framing's ANIM.TRIP: a bounce as he lands, a
        # hop as he gets up), flashing as the arrow strikes
        at = secs(f - hit)
        k = 1 - (1 - min(1.0, at / KNOCK_TIME)) ** 2
        x, y = MAN_AT[0] + (dx - MAN_AT[0]) * k, MAN_AT[1] + (dy - MAN_AT[1]) * k
        d, flip = facing(SHOT[0] - MAN_AT[0], SHOT[1] - MAN_AT[1])
        lying = TRIP_FALL <= at <= DOWN_FOR - TRIP_RISE
        frame = "trip0" if not lying else "trip1" if int(at / KICK_STEP) % 2 == 0 else "trip2"
        landed = at - TRIP_FALL
        bounce = math.sin(landed / 0.2 * math.pi) * 2 if 0 < landed < 0.2 else 0
        hop = math.sin((DOWN_FOR - at) / TRIP_RISE * math.pi) * 3 if at > DOWN_FOR - TRIP_RISE else 0
        tint = HURT_TINT if at < HURT_FOR else None
        return Man(x, y, f"kin_6_{frame}_{d}", flip, hp, round(bounce + hop), tint, lying)
    if f < MAN_WALKS:  # bending for his apple
        ax, ay = knocked()[1]
        d, flip = facing(ax - dx, ay - dy)
        return Man(dx, dy, f"kin_6_bend_{d}", flip, hp, 0, None, False)
    if f < MAN_SITS:
        return Man(*walking(6, f, MAN_WALKS, MAN_SITS, (dx, dy), MAN_SEAT), hp, 0, None, False)
    if f < MAN_UP:
        d, flip = facing(FIRE[0] - MAN_SEAT[0], FIRE[1] - MAN_SEAT[1])
        return Man(*MAN_SEAT, f"kin_6_sit_{d}", flip, hp, 0, None, False)
    return Man(*walking(6, f, MAN_UP, MAN_BACK, MAN_SEAT, MAN_AT), hp, 0, None, False)


def apple_at(f: int):
    """Where the apple is in frame `f`, if it is, and what it sorts by: shaken down off the cherry
    at its blow onto the man's head, knocked off it by the arrow and lying in the grass, then in
    his hands to the fire, until he eats it there."""
    drop, hit = blow_frames()[APPLE_BLOW], man_hit()
    if not drop <= f < HEARTS[0]:
        return None
    man = man_at(f)
    if f <= hit:  # falling onto his head, then on it
        head = man.y - APPLE_ON["stand"]
        hx, hy = TREE_AT[0] + FRUIT[0], TREE_AT[1] + FRUIT[1]
        age = secs(f - drop)
        y = min(head, hy + 130 * age * age)
        return (hx + (man.x - hx) * min(1.0, age / 0.3) if y < head else man.x), y, man.y + 0.2
    if f < MAN_WALKS:  # knocked off in an arc (render/fx.ts toss), then in the grass before him
        (lx, ly), t = knocked()[1], secs(f - hit)
        u = min(1.0, t / APPLE_FLIGHT)
        z0 = APPLE_ON["stand"]
        z = max(0.0, z0 + (TOSS_FALL * APPLE_FLIGHT / 2 - z0 / APPLE_FLIGHT) * t - TOSS_FALL * t * t / 2)
        gy = MAN_AT[1] + (ly - MAN_AT[1]) * u
        return MAN_AT[0] + (lx - MAN_AT[0]) * u, gy - z, gy
    if f < MAN_SITS:  # held before him as he walks
        return man.x + (-4 if man.flip else 4), man.y - APPLE_HELD, man.y + 0.2
    return man.x + 4, man.y - APPLE_HELD, man.y + 0.2


def nm_ground() -> Image.Image:
    """The glade's meadow, darker away from the camp, speckled with lighter grass."""
    im = Image.new("RGBA", (NM_W, NM_H))
    px = im.load()
    for y in range(NM_H):
        for x in range(NM_W):
            dx, dy = (x - FIRE[0]) / 130, (y - FIRE[1]) / 70
            d = dx * dx + dy * dy + (noise(x, y, 7) - 0.5) * 0.18
            c = MEADOW[0 if d < 0.2 else 1 if d < 0.55 else 2 if d < 1.0 else 3]
            px[x, y] = (*((0x7A, 0xAD, 0x52) if noise(x, y, 3) < 0.04 else c), 255)
    return im


def radial(rx: int, ry: int, peak: int, inner: float) -> Image.Image:
    """A soft disc of light, flattened as the ground is seen, centred: 0..`peak` a pixel."""
    im = Image.new("L", (2 * rx + 1, 2 * ry + 1))
    px = im.load()
    for y in range(im.height):
        for x in range(im.width):
            d = math.hypot((x - rx) / rx, (y - ry) / ry)
            px[x, y] = round(peak * (1 - smoothstep(inner, 1.0, d)))
    return im


def nm_masks():
    """The vignette, and the fire's pool and a kin's sight as discs to lay where they are."""
    vignette = Image.new("L", (NM_W, NM_H))
    vp = vignette.load()
    for y in range(NM_H):
        for x in range(NM_W):
            vx, vy = (x + 0.5) / NM_W - 0.5, ((y + 0.5) / NM_H - 0.5) * 0.85
            vp[x, y] = round(255 * (0.6 + 0.4 * smoothstep(0.62, 0.22, math.hypot(vx, vy))))
    pools = {k: radial(round(POOL[0] * (1 + (STOKED_LIGHT - 1) * k / 4)), round(POOL[1] * (1 + (STOKED_LIGHT - 1) * k / 4)), 255, 0.35)
             for k in range(5)}
    return vignette, pools, radial(*SIGHT, 220, 0.3)


def glow_of(h: float) -> Image.Image:
    """The campfire glow's shader for a flame `h` tall: a halo round it and a wide, flat one on the ground."""
    rx, ry = round(h * 2.2), round(h * 1.6)
    im = Image.new("L", (2 * rx + 1, 2 * ry + 1))
    px = im.load()
    for y in range(im.height):
        for x in range(im.width):
            lx, ly = x - rx + 0.5, y - ry + 0.5
            halo = max(1 - math.hypot(lx / (h * 1.2), (ly + h * 0.45) / (h * 1.05)), 0) ** 2.2
            ground = max(1 - math.hypot(lx / (h * 2.1), ly / (h * 0.8)), 0) ** 1.7 * 0.75
            px[x, y] = round(255 * max(halo, ground))
    return im


def nm_scene(sheet: Sheet, ground: Image.Image, f: int) -> Image.Image:
    im = ground.copy()
    stand = [(y, lambda n=n, x=x, y=y: sheet.put(im, n, x, y)) for n, x, y in TREES + TUFTS]
    for n, x, y in TREES:
        sheet.put(im, "shadow_tree", x, y)

    def figure(x, y, name, flip):
        sheet.put(im, "shadow_m", x, y)
        stand.append((y, lambda: sheet.put(im, name, x, y, flip)))

    for k, x, y, d, flip in CIRCLE:
        figure(x, y, f"kin_{k}_sit_{d}", flip)
    figure(*archer_at(f))
    plan = woodcutter_plan(sheet.flame["stoked"])
    x, y, name, flip, holding = woodcutter_at(f, sheet.flame["stoked"])
    figure(x, y, name, flip)
    if holding:  # the log in his arms, over his front
        stand.append((y + 0.1, lambda x=x, y=y: sheet.put(im, "stoke_log", x, y - 9)))
    man = man_at(f)
    sheet.put(im, "shadow_m", man.x, man.y)
    stand.append((man.y, lambda: sheet.put(im, man.name, man.x, man.y - man.lift, man.flip, man.tint)))
    if man.lying:  # stars circling over his head (draw/kin.ts dazed)
        sx, sy = LYING_STARS[int(man.name[-1])]
        stars = f"stun_{int(secs(f) * 10) % 4}"
        stand.append((man.y + 0.1, lambda: sheet.put(im, stars, man.x + (-sx if man.flip else sx), man.y - man.lift + sy)))
    apple = apple_at(f)
    if apple:
        ax, ay, by = apple
        stand.append((by, lambda: sheet.put(im, "apple", ax, ay)))
    # the cherry: shaken by the axe, falling, or a stump with its logs (until he takes them up)
    state, k = tree_state(f)
    tx, ty = TREE_AT
    tree = TREE
    if state == "standing":
        sx = round(math.sin(secs(f) * 60) * k * 4)
        sheet.put(im, "shadow_tree", tx, ty)
        stand.append((ty, lambda: sheet.put(im, tree, tx + sx, ty)))
    else:
        stand.append((ty - 0.5, lambda: sheet.put(im, STUMP, tx, ty)))
        if state == "falling":
            # the crown swings down towards where it falls, turned and stretched as the game does
            th = k * k * math.pi / 2
            a = FALL_DIR * math.pi / 4
            fx = math.cos(a) * math.sin(th) / UPRIGHT
            fy = -math.cos(th) + math.sin(a) * math.sin(th) * GROUND_Y / UPRIGHT
            turned = sheet.turned(tree, math.degrees(math.atan2(fx, -fy)), max(0.2, math.hypot(fx, fy)))
            stand.append((ty, lambda: sheet.put(im, turned, tx, ty)))
        elif f < plan["carry"]:  # its logs, until he takes them up
            for i, log in enumerate(LOGS):
                lx, ly, by = log_at(i)
                stand.append((by, lambda log=log, lx=lx, ly=ly: sheet.put(im, f"{log}_{FALL_DIR}", lx, ly)))
    stand.append((FIRE[1], lambda: sheet.put(im, "campfire", *FIRE)))
    stand.append((FIRE[1] + 0.5, lambda: sheet.put(im, f"flame_{f}", FIRE[0], FIRE[1] - 1)))
    king = f"king_idle{f * KING_IDLE // NM_FRAMES}_{KING_DIR}"
    sheet.put(im, "shadow_l", *KING_AT)
    stand.append((KING_AT[1], lambda: sheet.put(im, king, *KING_AT, True)))
    for _, draw in sorted(stand, key=lambda s: s[0]):
        draw()
    return im


def stoke_share(sheet: Sheet, f: int) -> float:
    """How far stoked the fire is in frame `f`, 0..1, by how tall the flame burns."""
    tall = sheet.flame["tall"]
    lo, hi = min(tall), max(tall)
    return (tall[f] - lo) / (hi - lo) if hi > lo else 0.0


def nm_light(sheet: Sheet, scene: Image.Image, masks, glows: dict, f: int) -> Image.Image:
    vignette, pools, sight = masks
    t = 2 * math.pi * f / NM_FRAMES  # whole turns a loop, so the flicker wraps with it
    flick = 0.86 + 0.09 * math.sin(18 * t + 1.3) + 0.05 * math.sin(43 * t + 3.9)
    share = stoke_share(sheet, f)
    light = Image.new("L", scene.size)
    pool = pools[round(share * 4)].point(lambda v: v * (0.92 + 0.08 * flick))
    light.paste(pool, (FIRE[0] - pool.width // 2, FIRE[1] - pool.height // 2))
    # the night lifted round each kin out of the fire's light, as a kin's sight lifts it
    for x, y, *_ in (archer_at(f), woodcutter_at(f, sheet.flame["stoked"]), man_at(f)):
        seen = Image.new("L", scene.size)
        seen.paste(sight, (round(x) - SIGHT[0], round(y) - SIGHT_UP - SIGHT[1]))
        light = ImageChops.lighter(light, seen)
    lit = Image.composite(Image.new("RGB", scene.size, LAMPLIGHT), Image.new("RGB", scene.size, NIGHT_FOG), light)
    lit = ImageChops.multiply(lit, Image.merge("RGB", [vignette] * 3))
    out = ImageChops.multiply(scene.convert("RGB"), lit)
    h = round(sheet.flame["tall"][f])
    if h not in glows:
        glows[h] = glow_of(h)
    g = glows[h].point(lambda v: v * GLOW * flick)
    layer = Image.new("L", scene.size)
    layer.paste(g, (FIRE[0] - g.width // 2, FIRE[1] - g.height // 2))
    return ImageChops.add(out, ImageChops.multiply(Image.new("RGB", scene.size, GLOW_TINT), Image.merge("RGB", [layer] * 3)))


def burst(im: Image.Image, f: int, start: int, at, n: int, speed: float, up: float, life: float, colour, seed: int, gravity: float = 120) -> None:
    """A burst of specks thrown up from `at` in frame `start` and falling back (render/fx.ts burst)."""
    age = secs(f - start)
    if not 0 <= age < life:
        return
    px = im.load()
    for i in range(n):
        a = math.pi * (0.1 + 0.8 * noise(i, start, seed))
        v = speed * (0.6 + 0.4 * noise(i, start, seed + 1))
        x = at[0] + math.cos(a) * v * age * (1 if noise(i, start, seed + 2) < 0.5 else -1)
        y = at[1] - math.sin(a) * up * age + gravity * age * age
        if 0 <= x < NM_W and 0 <= y < NM_H:
            px[int(x), int(y)] = colour


def nm_effects(sheet: Sheet, im: Image.Image, f: int) -> None:
    """What flies, floats and shows over the world, unlit: chips, leaves, the arrow, blood, the
    man's bar and hearts, the thrown log and the fire flaring up, STOKED, embers."""
    tx, ty = TREE_AT
    for b in blow_frames():
        burst(im, f, b, (tx, ty - 6), 4, 55, 60, 0.5, (196, 150, 92), 51)  # fx_chip, fx_splinter
    fell = blow_frames()[-1] + frames_of(FALL_TIME)
    a = FALL_DIR * math.pi / 4  # EV.TREE_FELL: its leaves, where its crown came down
    burst(im, f, fell, (tx + math.cos(a) * 30, ty + math.sin(a) * 30 * GROUND_Y), 14, 60, 50, 0.9, BLOSSOM, 52, 40)
    # the man landing on his back (fx-tools.ts EV.TRIP): dust and grass
    lands, (dx, dy) = man_hit() + frames_of(TRIP_FALL), knocked()[0]
    burst(im, f, lands, (dx, dy), 8, 40, 20, 0.6, DUST, 56, 60)
    burst(im, f, lands, (dx, dy - 2), 4, 35, 45, 0.5, GRASS, 57)
    lit = im.convert("RGBA")
    man = man_at(f)
    mx, my, hp = man.x, man.y, man.hp
    # the arrow at the apple, into the man
    loose = looses()
    hit = loose + ARROW_FLIGHT
    if loose <= f < hit:
        u = (f - loose + 1) / ARROW_FLIGHT
        sx, sy = SHOT[0] + BOW_HAND[0], SHOT[1] + BOW_HAND[1]
        ex, ey = MAN_AT[0] + 2, MAN_AT[1] - 9
        arrow = sheet.turned("arrow", math.degrees(math.atan2(ey - sy, ex - sx)))
        sheet.put(lit, arrow, sx + (ex - sx) * u, sy + (ey - sy) * u)
    # his bar while he is short of his most (healthBar: a dark bar with his HP across its middle)
    if hit <= f and hp < MAN_HP:
        d = ImageDraw.Draw(lit)
        w = MAN_HP * 2
        # lower over him lying or sitting, so it hangs over him and not over the kin behind
        left, top = round(mx - w / 2), round(my - (19 if man.lying else 21 if "_sit_" in man.name else 25))
        d.rectangle([left - 1, top, left + w, top + 2], fill=(*OUTLINE, 255))
        d.rectangle([left, top + 1, left + round(w * hp / MAN_HP) - 1, top + 1], fill=(*HEAL, 255))
    for h in HEARTS:  # EV.HEAL: a heart rising from over his head
        age = secs(f - h)
        if 0 <= age < 1:
            sheet.put(lit, "fx_heart", MAN_SEAT[0], MAN_SEAT[1] - 14 - 14 * age)
    # the woodcutter's log, thrown in an arc onto the fire, turning as it flies (render/fx.ts toss)
    stoked = sheet.flame["stoked"]
    leave = woodcutter_plan(stoked)["leave"]
    if leave <= f < stoked:
        t = secs(f - leave + 1)
        u = min(1.0, t / LOG_FLIGHT)
        x = TOSS_AT[0] + (FIRE[0] - TOSS_AT[0]) * u
        y = TOSS_AT[1] + (FIRE[1] - 2 - TOSS_AT[1]) * u
        z = HAND_Z + (TOSS_FALL * LOG_FLIGHT / 2 - HAND_Z / LOG_FLIGHT) * t - TOSS_FALL * t * t / 2
        log = sheet.turned("stoke_log", 360 * LOG_SPIN * u)
        sheet.put(lit, log, x, y - z)
    age = secs(f - lands)
    if 0 <= age < 1.1 and (age < 0.7 or (f - lands) % 2 == 0):  # WHOOPS! floats up and fades
        sheet.text(lit, "WHOOPS!", dx, dy - 28 - 14 * age, WHOOPS)
    im.paste(lit.convert("RGB"))
    hit_at = (MAN_AT[0], MAN_AT[1] - 9)
    burst(im, f, hit, hit_at, 3, 45, 50, 0.5, (176, 32, 40), 53)  # EV.HIT's blood
    burst(im, f, stoked, (FIRE[0], FIRE[1] - 4), 18, 45, 100, 1.1, EMBER[2], 54, 60)  # LAND.FLARE: embers
    burst(im, f, stoked, (FIRE[0], FIRE[1] - 6), 8, 40, 70, 0.5, EMBER[0], 55, 60)  # and sparks
    age = secs(f - stoked)
    if 0 <= age < 1.1 and (age < 0.7 or (f - stoked) % 2 == 0):  # STOKED floats up and fades
        lit = im.convert("RGBA")
        sheet.text(lit, "STOKED", FIRE[0], FIRE[1] - 26 - 14 * age, MEAT_LIGHT)
        im.paste(lit.convert("RGB"))
    nm_embers(sheet, im, f)


def nm_embers(sheet: Sheet, im: Image.Image, f: int) -> None:
    """Sparks rising off the fire, each on a loop of its own that fits the card's; more off a stoked one."""
    px = im.load()
    many = 18 + round(18 * stoke_share(sheet, f))
    for i in range(many):
        life = 14 + int(noise(i, 1, 21) * 12)
        age = (f + i * 7) % NM_FRAMES
        if age >= life:
            continue
        u = age / life
        x = FIRE[0] + (noise(i, 2, 22) - 0.5) * 10 + math.sin(u * 5 + i) * 3 * u
        y = FIRE[1] - 6 - age * (0.9 + noise(i, 3, 23) * 0.6)
        if 0 <= x < NM_W and 0 <= y < NM_H:
            px[int(x), int(y)] = EMBER[min(len(EMBER) - 1, int(u * len(EMBER)))]


def nm_fireflies(im: Image.Image, f: int) -> None:
    """Fireflies in the dark round the glade: a slow loop each, glowing up and fading out."""
    px = im.load()
    for x0, y0, i in FIREFLIES:
        t = 2 * math.pi * (f / NM_FRAMES + noise(i, 4, 31))
        glow = max(0.0, math.sin(t * 5 + i))  # five times a loop, dark half the time
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
    t = secs(f) % HOP_TIME / HOP_TIME
    pose = stepped(HOP_POSE, t)
    return pose, stepped(HOP_LIFT, t), pose == "bend"


def nm_play(sheet: Sheet, im: Image.Image, f: int) -> None:
    """PLAY and the hopper's shadow on it, over the night (they are no part of it): the game's
    shadow, smaller while he is high, as the curtain's shadow shrinks under him."""
    x0, y0, x1, y1 = BUTTON
    _, lift, down = hop_at(f)
    d = ImageDraw.Draw(im)
    d.rectangle([x0, y0 + 1, x1, y1 + 1], fill=PLAY_LIP)
    d.rectangle([x0, y0 + down, x1, y1 + down], fill=PLAY_DOWN if down else PLAY_UP, outline=NM_INK)
    lit = im.convert("RGBA")
    sheet.put(lit, "shadow_s" if lift >= 8 else "shadow_m", (x0 + x1 + 1) / 2, STAND_ON + down)
    im.paste(lit.convert("RGB"))


def nm_hopper(sheet: Sheet, f: int) -> Image.Image:
    """The kinsman hopping on PLAY, to be laid over the word on it."""
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
    down = hop_at(f)[2] * G // S
    x = (x0 + x1 + G / S - big.getlength("PLAY") - 3 * PLAY_SPACING) / 2
    for ch in "PLAY":
        x = put_text(d, x, round((y0 + y1 + G / S) / 2 - 5) + down, ch, NM_INK, big) + PLAY_SPACING
    return im


def nm_palette(scenes: list[Image.Image], over: list[Image.Image]) -> Image.Image:
    """The scene's shades by octree (it keeps the small bright ones, the crown's gold and the
    tribe's blue, that median cut gives up), then every colour of the text and the hopper as it
    is. The count's digits share their colour with the rest of the text, so a new count leaves the
    palette as it was and changes the GIF only where it is written."""
    keep = sorted({c[:3] for im in over for _, c in im.getcolors(1 << 16) if c[3] == 255})
    mosaic = Image.new("RGB", (NM_W, NM_H * len(scenes)))
    for i, im in enumerate(scenes):
        mosaic.paste(im, (0, i * NM_H))
    room = 256 - len(keep)
    shades = mosaic.quantize(room, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE)
    flat = shades.getpalette()[: 3 * room] + [v for c in keep for v in c]
    pal = Image.new("P", (1, 1))
    pal.putpalette(flat)
    return pal


def nomads_card(stats: dict) -> tuple[list[Image.Image], Image.Image]:
    """The card's frames and their palette."""
    players = stats.get("nomads", {}).get("players")
    sheet = Sheet()
    ground, masks, glows = nm_ground(), nm_masks(), {}
    frames, scenes, over = [], [], []
    for f in range(NM_FRAMES):
        im = nm_light(sheet, nm_scene(sheet, ground, f), masks, glows, f)
        nm_effects(sheet, im, f)
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
