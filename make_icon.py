"""Generate typer_icon.ico (a typewriter) and a preview PNG. Run once."""
from PIL import Image, ImageDraw

ACCENT = (67, 201, 138)      # emerald, matches the UI
ACCENT_D = (33, 150, 98)
DARK = (18, 22, 28)
PAPER = (240, 243, 245)
KEY = (231, 234, 238)
INK = (12, 16, 20)

S = 1024  # supersample canvas, downscaled later for crisp edges


def rr(d, box, r, fill):
    d.rounded_rectangle(box, radius=r, fill=fill)


def draw(img):
    d = ImageDraw.Draw(img)
    # background rounded square
    rr(d, (0, 0, S, S), int(S * 0.22), DARK)
    cx = S // 2

    # sheet of paper rising from the platen
    pw, ph = int(S * 0.40), int(S * 0.34)
    px = cx - pw // 2
    py = int(S * 0.10)
    rr(d, (px, py, px + pw, py + ph), int(S * 0.02), PAPER)
    # a couple of typed lines on the paper
    for i, frac in enumerate((0.45, 0.62, 0.79)):
        ly = py + int(ph * frac)
        lw = pw - int(S * 0.10) - (i * int(S * 0.04))
        d.rounded_rectangle((px + int(S * 0.05), ly, px + int(S * 0.05) + lw, ly + int(S * 0.018)),
                            radius=int(S * 0.009), fill=(176, 183, 191))

    # platen (the roller) behind the body
    plat_y = int(S * 0.44)
    rr(d, (int(S * 0.20), plat_y, int(S * 0.80), plat_y + int(S * 0.11)), int(S * 0.055), (60, 68, 78))
    # platen knob
    d.ellipse((int(S * 0.15), plat_y + int(S * 0.012), int(S * 0.235), plat_y + int(S * 0.097)), fill=(90, 99, 110))
    d.ellipse((int(S * 0.765), plat_y + int(S * 0.012), int(S * 0.85), plat_y + int(S * 0.097)), fill=(90, 99, 110))

    # typewriter body: a trapezoid (wider at the bottom)
    by0 = int(S * 0.52)
    by1 = int(S * 0.82)
    top_l, top_r = int(S * 0.24), int(S * 0.76)
    bot_l, bot_r = int(S * 0.15), int(S * 0.85)
    d.polygon([(top_l, by0), (top_r, by0), (bot_r, by1), (bot_l, by1)], fill=ACCENT)
    # base slab
    rr(d, (int(S * 0.13), by1 - int(S * 0.02), int(S * 0.87), by1 + int(S * 0.06)), int(S * 0.03), ACCENT_D)

    # keys: three rows of round keys on the sloped body
    rows = [
        (0.60, 0.30, 0.70, 6),
        (0.655, 0.275, 0.725, 6),
        (0.71, 0.25, 0.75, 5),
    ]
    kr = int(S * 0.021)
    for yfrac, xl, xr, n in rows:
        ky = int(S * yfrac)
        xs = [xl + (xr - xl) * (j / (n - 1)) for j in range(n)]
        for xf in xs:
            kx = int(S * xf)
            d.ellipse((kx - kr, ky - kr, kx + kr, ky + kr), fill=INK)
            d.ellipse((kx - kr + int(S*0.004), ky - kr + int(S*0.004), kx + kr - int(S*0.004), ky + kr - int(S*0.004)), fill=KEY)
    # space bar
    sb_y = int(S * 0.765)
    rr(d, (int(S * 0.34), sb_y, int(S * 0.66), sb_y + int(S * 0.028)), int(S * 0.014), KEY)


def main():
    big = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    draw(big)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    imgs = [big.resize((s, s), Image.LANCZOS) for s in sizes]
    imgs[-1].save("typer_icon.ico", sizes=[(s, s) for s in sizes])
    big.resize((256, 256), Image.LANCZOS).save("typer_icon.png")
    print("wrote typer_icon.ico and typer_icon.png")


if __name__ == "__main__":
    main()
