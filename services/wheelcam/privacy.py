"""Privacy scrubbing for real order data (renders, order sheets, CAD) before it enters WheelCAM.

Real orders carry the CAD author's machine name, user, CPU and local paths (Parasolid and STEP
headers), customer and staff names (order sheets and their document properties) and image
metadata. Everything that leaves the raw asset folder goes through here:

- CAD headers keep only the format fields (schema, application, format)
- order sheets are reduced to engineering fields (size, ET, bore, PCD, hole form, blank, ...)
- images are re-encoded without metadata; regions can be blurred (logos on caps)
- `leaks` checks text or files against a private deny list that lives outside the repo
  (WHEELCAM_PRIVATE_TERMS, default ~/.wheelcam/private_terms.txt, one term per line)

The mapping from real order names to case ids is written outside the repo as well.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

REDACTED = "redacted"

# Parasolid transmit header (between **PART1; and **END_OF_HEADER): fields that identify the
# author's machine, account, folders or the customer.
XT_PRIVATE_KEYS = ("MC", "MC_MODEL", "MC_ID", "OS", "OS_RELEASE", "USER", "SITE", "FILE", "KEY", "DATE")


def scrub_xt(text: str) -> str:
    """Parasolid text with the private header fields redacted; the geometry is untouched."""
    head, sep, body = text.partition("**END_OF_HEADER")
    if not sep:
        raise ValueError("not a Parasolid transmit file (no END_OF_HEADER)")
    keys = "|".join(XT_PRIVATE_KEYS)
    # A value runs to the ';' that ends the field; FILE paths may hold spaces and non-ASCII.
    head = re.sub(rf"(?m)^({keys})=[^;]*;", lambda m: f"{m.group(1)}={REDACTED};", head)
    return head + sep + body


def scrub_step(text: str) -> str:
    """STEP with FILE_NAME's name, author, organisation and authorisation redacted."""
    def fix(m):
        return (f"FILE_NAME('{REDACTED}',{m.group(2)},('{REDACTED}'),('{REDACTED}'),"
                f"{m.group(5)},{m.group(6)},'{REDACTED}');")
    pattern = (r"FILE_NAME\s*\(\s*('(?:[^']|'')*')\s*,\s*('(?:[^']|'')*')\s*,\s*(\([^)]*\))\s*,\s*(\([^)]*\))\s*,"
               r"\s*('(?:[^']|'')*')\s*,\s*('(?:[^']|'')*')\s*,\s*('(?:[^']|'')*')\s*\)\s*;")
    return re.sub(pattern, fix, text, count=1, flags=re.S)


# Order sheet columns kept, by the start of their header label (factory and customer sheets
# differ in order and wording). Everything else (customer, phone, address, reviewer, preparer,
# prices, document properties) is dropped.
ORDER_COLUMNS = (("规格", "size"), ("偏距", "et_mm"), ("中心孔", "center_bore_mm"), ("孔距", "pcd"),
                 ("PCD孔型", "hole_form"), ("孔型", "hole_form"), ("颜色", "finish"), ("数量", "qty"),
                 ("毛坯", "blank"), ("产品型号", "position"))
ORDER_FIELDS = {"气门嘴角度": "valve_angle_deg", "气嘴角度": "valve_angle_deg", "PCD孔深度": "pcd_hole_depth",
                "盖子型号": "cap", "标盖要求": "cap", "螺丝孔要求": "hole_seat"}
LOAD_RE = re.compile(r"MAX\s*LOAD\s*(\d+)\s*KG", re.I)
SIZE_RE = re.compile(r"(\d{2})\s*[X*x×]\s*(\d+(?:\.\d+)?)")
PCD_RE = re.compile(r"(\d+)\s*[/*xX×]\s*(\d+(?:\.\d+)?)")
CONCAVE_RE = re.compile(r"\b(MAX\s+(?:DEEP\s+)?CONCAVE)\b", re.I)


def _num(v):
    try:
        return float(str(v).replace("*", "").strip())
    except ValueError:
        return None


def _column(label: str):
    label = label.replace(" ", "")
    return next((key for start, key in ORDER_COLUMNS if label.startswith(start)), None)


def order_from_rows(rows) -> dict:
    """Engineering fields of an order sheet, from its rows (lists of cell values, by column).

    {"variants": [{diameter_in, width_in, et_mm, center_bore_mm, bolts, pcd_mm, hole_form, finish,
    qty, blank, position}], valve_angle_deg, pcd_hole_depth, cap, hole_seat, max_load_kg, style}.
    No names, codes, contacts or prices."""
    rows = [[("" if c is None else str(c).strip()) for c in row] for row in rows]
    header, variants, out, style = None, [], {}, []
    for row in rows:
        cells = [c for c in row if c]
        if not cells:
            continue
        for c in cells:                     # notes may share a row with a product
            m = LOAD_RE.search(c)
            if m:
                out["max_load_kg"] = int(m.group(1))
            m = CONCAVE_RE.search(c)
            if m and m.group(1).upper() not in style:
                style.append(m.group(1).upper())
        if header is None and sum(_column(c) is not None for c in row) >= 4:
            header = [_column(c) if c else None for c in row]
            last = max(i for i, k in enumerate(header) if k)
            if "blank" not in header and last + 1 < len(header):
                header[last + 1] = "blank"          # an unlabelled column after qty holds the blank
            continue
        if header is not None and "size" in header:
            size = SIZE_RE.fullmatch(row[header.index("size")]) if header.index("size") < len(row) else None
            if size:
                values = {k: row[i] for i, k in enumerate(header) if k and i < len(row) and row[i]}
                v = {"diameter_in": int(size.group(1)), "width_in": float(size.group(2))}
                for k in ("et_mm", "center_bore_mm"):
                    if _num(values.get(k, "")) is not None:
                        v[k] = _num(values[k])
                pcd = PCD_RE.fullmatch(values.get("pcd", ""))
                if pcd:
                    v["bolts"], v["pcd_mm"] = int(pcd.group(1)), float(pcd.group(2))
                for k in ("hole_form", "finish", "blank"):
                    if values.get(k):
                        v[k] = values[k]
                if _num(values.get("qty", "")) is not None:
                    v["qty"] = int(_num(values["qty"]))
                if values.get("position", "").upper() in ("FRONTS", "FRONT", "REARS", "REAR"):
                    v["position"] = values["position"].upper().rstrip("S").lower()
                variants.append(v)
                continue
        label = cells[0].replace(" ", "")
        for name, key in ORDER_FIELDS.items():
            if label == name and len(cells) > 1:
                out[key] = cells[1]
    out = {"variants": variants, **out}
    if style:
        out["style_notes"] = style
    return out


def clean_image(src, dst, blur=(), quality=92):
    """Re-encode `src` to `dst` without metadata (EXIF, XMP, text chunks). `blur`: circles
    (cx, cy, r) in pixels to blur heavily (logos on the centre caps)."""
    from PIL import Image, ImageDraw, ImageFilter
    im = Image.open(src)
    im = im.convert("RGBA" if im.mode in ("RGBA", "LA", "P") and str(dst).lower().endswith(".png") else "RGB")
    for circle in blur:
        cx, cy, r = (float(v) for v in circle)
        box = tuple(int(v) for v in (cx - r, cy - r, cx + r, cy + r))
        region = im.crop(box).filter(ImageFilter.GaussianBlur(max(4, r / 3)))
        mask = Image.new("L", region.size, 0)
        ImageDraw.Draw(mask).ellipse((0, 0, region.size[0] - 1, region.size[1] - 1), fill=255)
        im.paste(region, box[:2], mask)
    clean = im.copy()
    clean.info = {}                        # Pillow writes EXIF/ICC/text chunks from info by default
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.suffix.lower() in (".jpg", ".jpeg"):
        clean.convert("RGB").save(dst, "JPEG", quality=quality)
    else:
        clean.save(dst, "PNG")
    return dst


def private_terms(path=None) -> list[str]:
    """The deny list (never in the repo): names, customer codes, machine names, private folders."""
    path = Path(path or os.getenv("WHEELCAM_PRIVATE_TERMS", "~/.wheelcam/private_terms.txt")).expanduser()
    if not path.exists():
        return []
    return [t.strip() for t in path.read_text(encoding="utf-8").splitlines() if t.strip() and not t.startswith("#")]


def leaks(text: str, terms) -> list[str]:
    """The deny-list terms found in `text` (case-insensitive)."""
    low = text.lower()
    return [t for t in terms if t.lower() in low]


IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")


def _image_metadata(path) -> str:
    """Every metadata field of an image as text (info chunks and EXIF)."""
    from PIL import Image
    with Image.open(path) as im:
        parts = [f"{k}={v!r}" for k, v in im.info.items()]
        parts += [f"{k}={v!r}" for k, v in im.getexif().items()]
    return "\n".join(parts)


def scan(paths, terms) -> dict:
    """{file: [terms]} for every file under `paths` that holds a deny-list term (text files and
    other binaries by their bytes, images by their metadata)."""
    found = {}
    for root in paths:
        root = Path(root)
        for f in ([root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())):
            if any(part in {".git", ".venv", "node_modules", "__pycache__"} for part in f.parts):
                continue
            if f.suffix.lower() in IMAGE_SUFFIXES:
                text = _image_metadata(f)          # pixels are not text; short terms hit them by chance
            else:
                try:
                    data = f.read_bytes()
                except OSError:
                    continue
                text = data.decode("utf-8", errors="ignore") + data.decode("latin-1", errors="ignore")
            hit = leaks(text, terms)
            if hit:
                found[str(f)] = hit
    return found
