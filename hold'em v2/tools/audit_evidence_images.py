"""Post-run image audit of a validation evidence folder. METADATA ONLY: it reads file headers, never pictures. READ-ONLY.

    python tools/audit_evidence_images.py <evidence folder> [--json REPORT.json] [--limit FOLDER=WxH ...] [--all]

WHY

A validation run once saved three whole-screen captures (1920 x 1080) that may show private browser content. This audit is the check to apply after
any run: it lists every image in the evidence folder, says where it sits and how big it is, and flags whatever should not be there. It is meant to be
read by a person, who then decides; it never moves, crops, renames or deletes anything.

WHAT IT DOES

  * Walks the folder (symbolic links are not followed) and inventories every image file: by extension (.png .jpg .jpeg .gif .bmp .webp .tif .tiff .ico),
    and by signature for a file with any other extension that starts like a PNG, JPEG or GIF.
  * Reads each image's width and height from its FILE HEADER only (PNG IHDR, GIF logical screen, BMP header, JPEG start-of-frame marker): at most
    `HEADER_LIMIT` bytes of a file are ever read, and no pixel data is decoded, rendered, opened in a viewer or sent anywhere. A file whose
    picture data is damaged still reports its size, which is proof that the pictures themselves are not read.
  * Judges each image:
      WHOLE_SCREEN          at least `WHOLE_SCREEN_MIN` (400) pixels on either side, wherever it is. FAILS the audit.
      OVER_LIMIT            larger than its folder allows (the limits below). Needs review.
      UNEXPECTED_LOCATION   an image in a folder that is not listed. Needs review.
      UNREADABLE            the header is missing, malformed or the file cannot be read. Needs review.
      UNSUPPORTED           an image type whose size this audit does not read. Needs review.
      EXTENSION_MISMATCH    an image by signature that does not have an image extension. Needs review.
      OK                    inside its folder's limit.
  * Verdict: FAIL if anything is WHOLE_SCREEN or the folder cannot be read; REVIEW if anything else is not OK; PASS otherwise.
    Exit status: 0 PASS, 1 REVIEW, 2 FAIL, 3 the folder does not exist or the arguments are wrong.

PERMITTED SIZES (the defaults; `--limit FOLDER=WxH` replaces or adds one, and the table is printed with every report)

    panel_thumbnails           120 x 120    the panel-diagnostics thumbnails
    read_mismatches            220 x 260    card crops and their wider context crops
    home/logs/failures         160 x 160    the tracker's own refused-card crops (on whenever the copied configuration has debug_save_failures)
    home/recognition/templates  80 x 80     the seeded rank and suit templates

A folder limit applies to that folder and everything under it. Anything else is an unexpected location.

STOP FOR REVIEW

If the verdict is not PASS: leave everything exactly where it is, do not share the folder, and review the flagged files locally. This tool offers no
way to delete, move or edit a file. `--json` writes its report only OUTSIDE the evidence folder (it refuses a path inside it), so the audit never
adds a file to the evidence it checks.
"""

import argparse
import json
import os
import struct
import sys

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".ico")
HEADER_LIMIT = 65536                        # the most bytes ever read from any one file
SNIFF_BYTES = 12                            # read from a file with a non-image extension, to see whether it is an image anyway
WHOLE_SCREEN_MIN = 400                      # a side this long or longer is a screen capture, not a crop: the audit fails
FOLDER_LIMITS = (("panel_thumbnails", 120, 120), ("read_mismatches", 220, 260), ("home/logs/failures", 160, 160),
                 ("home/recognition/templates", 80, 80))

OK, OVER_LIMIT, UNEXPECTED_LOCATION, WHOLE_SCREEN, UNREADABLE, UNSUPPORTED, EXTENSION_MISMATCH = (
    "OK", "OVER_LIMIT", "UNEXPECTED_LOCATION", "WHOLE_SCREEN", "UNREADABLE", "UNSUPPORTED", "EXTENSION_MISMATCH")
PASS, REVIEW, FAIL = "PASS", "REVIEW", "FAIL"
EXIT = {PASS: 0, REVIEW: 1, FAIL: 2}
EXIT_USAGE = 3


class HeaderError(Exception):
    """The header could not be read or is not a valid image header (the message says why)."""


class Unsupported(Exception):
    """An image type whose size this audit does not read."""


class _Reader:
    """Reads a file's first bytes, never more than HEADER_LIMIT in all."""

    def __init__(self, handle):
        self.handle, self.used = handle, 0

    def take(self, count):
        if self.used + count > HEADER_LIMIT:
            raise HeaderError("no image header within the first %d bytes" % HEADER_LIMIT)
        data = self.handle.read(count)
        self.used += len(data)
        if len(data) < count:
            raise HeaderError("the file ends inside its header")
        return data


def _png(reader):
    reader.take(8)                                              # the signature was checked by the caller
    length, kind = struct.unpack(">I4s", reader.take(8))
    if kind != b"IHDR" or length != 13:
        raise HeaderError("a PNG whose first chunk is not a valid IHDR")
    width, height = struct.unpack(">II", reader.take(8))
    return "png", width, height


def _gif(reader):
    data = reader.take(10)
    return "gif", struct.unpack("<H", data[6:8])[0], struct.unpack("<H", data[8:10])[0]


def _bmp(reader):
    data = reader.take(26)
    size = struct.unpack("<I", data[14:18])[0]
    if size == 12:
        return "bmp", struct.unpack("<H", data[18:20])[0], struct.unpack("<H", data[20:22])[0]
    width, height = struct.unpack("<ii", data[18:26])
    return "bmp", abs(width), abs(height)


def _jpeg(reader):
    reader.take(2)
    while True:
        byte = reader.take(1)
        if byte != b"\xff":
            raise HeaderError("a JPEG with a stray byte between its segments")
        marker = reader.take(1)
        while marker == b"\xff":                                # fill bytes
            marker = reader.take(1)
        code = marker[0]
        if code in (0x00, 0x01) or 0xD0 <= code <= 0xD8:
            continue                                            # markers without a length
        if code in (0xD9, 0xDA):
            raise HeaderError("a JPEG whose picture data begins before any frame header")
        length = struct.unpack(">H", reader.take(2))[0]
        if length < 2:
            raise HeaderError("a JPEG segment with an impossible length")
        if code in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            _precision, height, width = struct.unpack(">BHH", reader.take(5))
            return "jpeg", width, height
        reader.take(length - 2)                                 # skip this segment's body


SIGNATURES = ((b"\x89PNG\r\n\x1a\n", _png), (b"\xff\xd8\xff", _jpeg), (b"GIF87a", _gif), (b"GIF89a", _gif))


def read_header(path):
    """(format, width, height) from the file's header only. Raises HeaderError (cannot read, malformed) or Unsupported."""
    try:
        with open(path, "rb") as handle:
            reader = _Reader(handle)
            start = handle.read(8)
            handle.seek(0)
            for signature, parse in SIGNATURES:
                if start.startswith(signature):
                    return parse(reader)
            if start.startswith(b"BM"):
                return _bmp(reader)
    except OSError as exc:
        raise HeaderError("the file cannot be read (%s)" % exc.__class__.__name__)
    except struct.error:
        raise HeaderError("a damaged header")
    if os.path.splitext(path)[1].lower() in (".webp", ".tif", ".tiff", ".ico"):
        raise Unsupported("an image type whose size is not read by this audit")
    raise HeaderError("not a recognisable image header")


def sniff(path):
    """True when a file that does not have an image extension starts like a PNG, JPEG or GIF."""
    try:
        with open(path, "rb") as handle:
            start = handle.read(SNIFF_BYTES)
    except OSError:
        return False
    return any(start.startswith(signature) for signature, _parse in SIGNATURES)


def folder_rule(relative_path, limits):
    """(folder, max width, max height) of the longest listed folder that contains the path, or None."""
    normal = relative_path.replace("\\", "/").lower()
    best = None
    for folder, max_width, max_height in limits:
        prefix = folder.replace("\\", "/").lower().rstrip("/") + "/"
        if normal.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, folder, max_width, max_height)
    return None if best is None else best[1:]


def judge(relative_path, width, height, limits=FOLDER_LIMITS, whole_screen_min=WHOLE_SCREEN_MIN):
    """(status, detail) for an image of this size at this path."""
    if width >= whole_screen_min or height >= whole_screen_min:
        return WHOLE_SCREEN, "%dx%d is at least %d pixels on a side" % (width, height, whole_screen_min)
    rule = folder_rule(relative_path, limits)
    if rule is None:
        return UNEXPECTED_LOCATION, "an image in a folder that has no listed limit"
    folder, max_width, max_height = rule
    if width > max_width or height > max_height:
        return OVER_LIMIT, "%dx%d exceeds the %dx%d allowed in %s" % (width, height, max_width, max_height, folder)
    return OK, "within the %dx%d allowed in %s" % (max_width, max_height, folder)


def audit(root, limits=FOLDER_LIMITS, whole_screen_min=WHOLE_SCREEN_MIN):
    """The audit of every image under `root`. Reads headers only; changes nothing."""
    images, skipped_links, other_files = [], [], 0
    for folder, dirs, files in sorted(os.walk(root, followlinks=False)):
        for name in sorted(dirs):
            if os.path.islink(os.path.join(folder, name)):          # a link to a folder: listed, never entered
                skipped_links.append(os.path.relpath(os.path.join(folder, name), root).replace("\\", "/") + "/")
        for name in sorted(files):
            path = os.path.join(folder, name)
            relative = os.path.relpath(path, root).replace("\\", "/")
            if os.path.islink(path):
                skipped_links.append(relative)
                continue
            by_extension = os.path.splitext(name)[1].lower() in IMAGE_EXTENSIONS
            if not by_extension and not sniff(path):
                other_files += 1
                continue
            try:
                size = os.path.getsize(path)
            except OSError:
                size = None
            item = {"path": relative, "bytes": size, "format": None, "width": None, "height": None}
            try:
                item["format"], item["width"], item["height"] = read_header(path)
            except Unsupported as exc:
                item["status"], item["detail"] = UNSUPPORTED, str(exc)
            except HeaderError as exc:
                item["status"], item["detail"] = UNREADABLE, str(exc)
            else:
                item["status"], item["detail"] = judge(relative, item["width"], item["height"], limits, whole_screen_min)
                if not by_extension:
                    item["status"], item["detail"] = ((WHOLE_SCREEN if item["status"] == WHOLE_SCREEN else EXTENSION_MISMATCH),
                                                      item["detail"] + "; an image by signature with a non-image extension")
            images.append(item)
    counts = {}
    for item in images:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    verdict = FAIL if counts.get(WHOLE_SCREEN) else (REVIEW if any(status != OK for status in counts) else PASS)
    return {"evidence_dir": root, "verdict": verdict, "images": images, "counts": counts, "non_image_files": other_files,
            "symbolic_links_not_followed": skipped_links, "whole_screen_min_pixels": whole_screen_min,
            "limits": [{"folder": f, "max_width": w, "max_height": h} for f, w, h in limits],
            "reads": "file headers only (at most %d bytes of any file); no picture was decoded, rendered or sent anywhere" % HEADER_LIMIT}


def render(report, show_all=False):
    """The report as text for a person."""
    lines = ["Image audit of %s" % report["evidence_dir"], "Reads: %s." % report["reads"], "",
             "Permitted sizes (a folder's limit covers everything under it; any other folder is an unexpected location; "
             "%d pixels on a side or more fails):" % report["whole_screen_min_pixels"]]
    lines += ["  %-28s %d x %d" % (rule["folder"], rule["max_width"], rule["max_height"]) for rule in report["limits"]]
    lines += ["", "%d image(s), %d other file(s). Counts by status: %s" % (len(report["images"]), report["non_image_files"],
                                                                         json.dumps(report["counts"], sort_keys=True))]
    flagged = [item for item in report["images"] if item["status"] != OK]
    shown = report["images"] if show_all else flagged
    for item in shown:
        size = "-" if item["width"] is None else "%dx%d" % (item["width"], item["height"])
        lines.append("  %-20s %-10s %9s bytes  %s  (%s)" % (item["status"], size, item["bytes"], item["path"], item["detail"]))
    if report["symbolic_links_not_followed"]:
        lines.append("Symbolic links not followed: %s" % ", ".join(report["symbolic_links_not_followed"]))
    lines += ["", "VERDICT: %s" % report["verdict"]]
    if report["verdict"] != PASS:
        lines.append("Stop here. Nothing was moved, changed or deleted and nothing will be. Do not share this folder; review the flagged files locally.")
    return "\n".join(lines)


def _limit(text):
    try:
        folder, size = text.split("=", 1)
        width, height = size.lower().split("x", 1)
        return folder.strip().replace("\\", "/").strip("/"), int(width), int(height)
    except ValueError:
        raise argparse.ArgumentTypeError("expected FOLDER=WIDTHxHEIGHT, for example panel_thumbnails=120x120, not %r" % text)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("evidence", help="the evidence folder to audit")
    parser.add_argument("--json", metavar="FILE", help="also write the report as JSON to FILE, which must be OUTSIDE the evidence folder")
    parser.add_argument("--limit", action="append", type=_limit, default=[], metavar="FOLDER=WxH",
                        help="replace or add one folder's permitted size (repeatable)")
    parser.add_argument("--all", action="store_true", help="list every image, not only the flagged ones")
    try:
        args = parser.parse_args(argv)
    except SystemExit as stop:
        return 0 if stop.code == 0 else EXIT_USAGE            # --help is not an error
    root = os.path.abspath(args.evidence)
    if not os.path.isdir(root):
        print("not a folder: %s" % root)
        return EXIT_USAGE
    if args.json:
        target = os.path.abspath(args.json)
        try:
            inside = os.path.commonpath([root, target]) == root
        except ValueError:                                     # another drive: certainly not inside
            inside = False
        if inside:
            print("refusing to write the report inside the evidence folder (%s): the audit never adds a file to the evidence it checks" % target)
            return EXIT_USAGE
    limits = {folder.lower(): (folder, w, h) for folder, w, h in FOLDER_LIMITS}
    for folder, width, height in args.limit:
        limits[folder.lower()] = (folder, width, height)
    report = audit(root, tuple(limits.values()))
    print(render(report, args.all))
    if args.json:
        with open(os.path.abspath(args.json), "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=1)
    return EXIT[report["verdict"]]


if __name__ == "__main__":
    sys.exit(main())
