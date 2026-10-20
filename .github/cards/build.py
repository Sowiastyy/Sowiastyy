"""Draws the profile README's animated project cards (assets/*.gif) from live numbers.

    python .github/cards/build.py            fetch the numbers, then draw
    python .github/cards/build.py --offline  draw from stats.json as it is

The cards workflow runs this once a day. Drawing is deterministic: the same
numbers give byte-identical GIFs, so a quiet day commits nothing.
"""
from __future__ import annotations

import json
import math
import os
import sys
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

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


def load_stats(offline: bool) -> dict:
    stats = json.loads(STATS.read_text()) if STATS.exists() else {}
    if offline:
        return stats
    for key, fetch in (("headlab", fetch_modrinth), ("headlab", fetch_curseforge), ("fleet", fetch_fleet)):
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


def save_gif(frames: list[Image.Image], path: Path, ms: int) -> None:
    """One palette for the whole loop (colours never flicker between frames), no dithering (flat stays flat)."""
    colors = set()
    for fr in frames:
        colors.update(c for _, c in fr.getcolors(1 << 24))
        if len(colors) > 256:
            break
    if len(colors) <= 256:
        flat = [v for c in sorted(colors) for v in c]
        pal = Image.new("P", (1, 1))
        pal.putpalette(flat + [0] * (768 - len(flat)))
    else:
        picks = [frames[i * (len(frames) - 1) // 3] for i in range(4)]
        mosaic = Image.new("RGB", (frames[0].width, frames[0].height * len(picks)))
        for i, fr in enumerate(picks):
            mosaic.paste(fr, (0, i * fr.height))
        pal = mosaic.quantize(256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    out = [fr.quantize(palette=pal, dither=Image.Dither.NONE) for fr in frames]
    out[0].save(path, save_all=True, append_images=out[1:], duration=ms, loop=0)
    print(f"{path.name}: {len(frames)} frames, {path.stat().st_size // 1024} KB")


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
        d.rectangle([232, 8, 240, 16], fill=(40, 40, 56), outline=INK)
        put_text(d, 235, 10, "K", INK)
        put_text(d, 245, 10, "OPEN EDITOR", MUTED)

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

    # the status line: keys on the left, the repo's numbers on the right
    t.spans(2, 12, [
        ("[F10]", TEXT), (" LEAVE FOCUS   ", FL_MUTED), ("F1-F9", TEXT), (" session   ", FL_MUTED),
        ("F11", TEXT), (" new", FL_MUTED),
    ])
    gap = ("   ", FL_MUTED)
    numbers = [
        (str(st["stars"]), TEXT), (" star" + "s" * (st["stars"] != 1), FL_MUTED), gap,
        (str(st["forks"]), TEXT), (" fork" + "s" * (st["forks"] != 1), FL_MUTED), gap,
        ("↓ ", ACCENT), (compact(st["downloads"]), TEXT), (" downloads", FL_MUTED), gap,
        (str(st["releases"]), TEXT), (" releases", FL_MUTED),
    ]
    col = right - 1 - (2 + sum(len(s) for s, _ in numbers))
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
    stats = load_stats("--offline" in sys.argv)
    ASSETS.mkdir(exist_ok=True)
    save_gif(headlab_card(stats), ASSETS / "headlab.gif", 70)
    save_gif(fleet_card(stats), ASSETS / "claude-fleet.gif", FL_MS)


if __name__ == "__main__":
    main()
