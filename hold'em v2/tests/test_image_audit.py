"""The post-run image audit (tools/audit_evidence_images.py): sizes from file headers only, folder limits, unexpected places, whole-screen images.

Why: a validation run once saved three whole-screen captures (1920 x 1080) that may show private browser content. The audit is the check to apply after any run. It
must read headers and never pictures, say where every image is and how big, flag whatever should not be there, fail on anything screen-sized, and never move,
change or delete a file or add one to the evidence it checks. These tests use temporary folders and hand-made headers only: no real evidence, screen or picture is read.
"""

import ast
import builtins
import hashlib
import importlib.util
import json
import os
import struct

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("audit_evidence_images", os.path.join(ROOT, "tools", "audit_evidence_images.py"))
audit_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit_mod)


# -- hand-made headers (no picture data worth decoding) ---------------------------------------------------------------------------------------------

def png_bytes(width, height, body=b"not pixel data"):
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I4sIIBBBBB", 13, b"IHDR", width, height, 8, 2, 0, 0, 0) + b"\x00\x00\x00\x00" + body


def jpeg_bytes(width, height, body=b"\x00" * 64):
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + app0 + sof + b"\xff\xda" + body


def gif_bytes(width, height):
    return b"GIF89a" + struct.pack("<HH", width, height) + b"\x00\x00\x00"


def bmp_bytes(width, height, core=False):
    if core:
        return b"BM" + b"\x00" * 12 + struct.pack("<IHHHH", 12, width, height, 1, 24)
    return b"BM" + b"\x00" * 12 + struct.pack("<IiiHH", 40, width, -height, 1, 24)


def put(root, relative, data):
    path = os.path.join(root, *relative.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)
    return path


def tree_state(root):
    """(relative path, size, mtime, sha256) of everything under root: if this is unchanged, nothing was touched."""
    out = []
    for folder, dirs, files in sorted(os.walk(root)):
        for name in sorted(files):
            path = os.path.join(folder, name)
            out.append((os.path.relpath(path, root), os.path.getsize(path), os.stat(path).st_mtime_ns, hashlib.sha256(open(path, "rb").read()).hexdigest()))
        out.append((os.path.relpath(folder, root), tuple(sorted(dirs))))
    return out


def good_tree(root):
    put(root, "panel_thumbnails/0001_first_player0.png", png_bytes(34, 47))
    put(root, "panel_thumbnails/0001_first_player0.json", b"{}")
    put(root, "read_mismatches/round0_dealer_1_crop.png", png_bytes(110, 122))
    put(root, "read_mismatches/round0_dealer_1_context.png", png_bytes(186, 214))
    put(root, "home/logs/failures/20261019-110000_dealer_1_unknown.png", png_bytes(112, 142))
    put(root, "home/recognition/templates/ranks/A.png", png_bytes(56, 76))
    put(root, "polls.jsonl", b'{"t": 0}\n')
    put(root, "report.md", b"# report\n")


# -- reading a header ----------------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("width,height", [(34, 47), (1, 1), (400, 300), (1920, 1080), (65535, 3)])
def test_a_png_size_comes_from_its_header_whatever_follows(tmp_path, width, height):
    path = put(tmp_path, "a.png", png_bytes(width, height))
    assert audit_mod.read_header(path) == ("png", width, height)


def test_the_other_formats_are_read_from_their_headers_too(tmp_path):
    assert audit_mod.read_header(put(tmp_path, "a.jpg", jpeg_bytes(640, 480))) == ("jpeg", 640, 480)
    assert audit_mod.read_header(put(tmp_path, "b.gif", gif_bytes(120, 90))) == ("gif", 120, 90)
    assert audit_mod.read_header(put(tmp_path, "c.bmp", bmp_bytes(100, 50))) == ("bmp", 100, 50)               # a negative height means top-down: still 50
    assert audit_mod.read_header(put(tmp_path, "d.bmp", bmp_bytes(64, 32, core=True))) == ("bmp", 64, 32)


def test_the_pictures_are_never_decoded_a_file_with_ruined_picture_data_still_gives_its_size(tmp_path):
    ruined = png_bytes(1920, 1080, body=os.urandom(200_000))
    assert audit_mod.read_header(put(tmp_path, "ruined.png", ruined)) == ("png", 1920, 1080)
    assert audit_mod.read_header(put(tmp_path, "ruined.jpg", jpeg_bytes(1280, 720, body=os.urandom(200_000)))) == ("jpeg", 1280, 720)


def test_no_more_than_the_header_limit_is_ever_read_from_a_file(tmp_path, monkeypatch):
    path = put(tmp_path, "big.png", png_bytes(1920, 1080, body=b"\x00" * 5_000_000))
    jpeg = put(tmp_path, "big.jpg", jpeg_bytes(1920, 1080, body=b"\x00" * 5_000_000))
    reads = []
    real_open = builtins.open

    class Counting:
        def __init__(self, handle):
            self.handle = handle

        def read(self, count=-1):
            data = self.handle.read(count)
            reads.append(len(data))
            return data

        def __getattr__(self, name):
            return getattr(self.handle, name)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.handle.close()

    def counting_open(file, mode="r", *args, **kwargs):
        handle = real_open(file, mode, *args, **kwargs)
        return Counting(handle) if str(file) in (path, jpeg) else handle
    monkeypatch.setattr(builtins, "open", counting_open)
    audit_mod.read_header(path)
    audit_mod.read_header(jpeg)
    assert reads and sum(reads) <= 2 * audit_mod.HEADER_LIMIT and max(reads) < 1000


@pytest.mark.parametrize("data", [b"", b"\x89PNG", b"\x89PNG\r\n\x1a\n" + b"\x00" * 8, b"\x89PNG\r\n\x1a\n" + struct.pack(">I4s", 13, b"IDAT") + b"\x00" * 20,
                                  b"\x89PNG\r\n\x1a\n" + struct.pack(">I4s", 7, b"IHDR") + b"\x00" * 20, b"plain text that says it is a picture", b"\xff\xd8\xff"])
def test_a_malformed_header_is_refused_with_a_reason(tmp_path, data):
    with pytest.raises(audit_mod.HeaderError) as why:
        audit_mod.read_header(put(tmp_path, "bad.png", data))
    assert str(why.value)


def test_a_jpeg_whose_picture_data_comes_before_any_frame_header_is_malformed(tmp_path):
    with pytest.raises(audit_mod.HeaderError):
        audit_mod.read_header(put(tmp_path, "x.jpg", b"\xff\xd8\xff\xda" + b"\x00" * 30))
    with pytest.raises(audit_mod.HeaderError):
        audit_mod.read_header(put(tmp_path, "y.jpg", b"\xff\xd8" + b"junk" * 10))


def test_a_missing_file_is_a_header_error_not_a_crash(tmp_path):
    with pytest.raises(audit_mod.HeaderError) as why:
        audit_mod.read_header(os.path.join(str(tmp_path), "gone.png"))
    assert "cannot be read" in str(why.value)


def test_image_types_whose_size_is_not_read_are_unsupported_not_guessed(tmp_path):
    for name in ("a.webp", "b.tif", "c.tiff", "d.ico"):
        with pytest.raises(audit_mod.Unsupported):
            audit_mod.read_header(put(tmp_path, name, b"RIFF....WEBPVP8 " + b"\x00" * 30))


# -- the folder limits ---------------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("path,width,height,status", [
    ("panel_thumbnails/a.png", 120, 120, "OK"), ("panel_thumbnails/a.png", 121, 120, "OVER_LIMIT"), ("panel_thumbnails/a.png", 120, 121, "OVER_LIMIT"),
    ("read_mismatches/a.png", 220, 260, "OK"), ("read_mismatches/a.png", 221, 260, "OVER_LIMIT"), ("read_mismatches/a.png", 220, 261, "OVER_LIMIT"),
    ("home/logs/failures/a.png", 160, 160, "OK"), ("home/logs/failures/a.png", 161, 160, "OVER_LIMIT"), ("home/logs/failures/a.png", 97, 142, "OK"),
    ("home/recognition/templates/a.png", 80, 80, "OK"), ("home/recognition/templates/ranks/a.png", 56, 76, "OK"), ("home/recognition/templates/suits/a.png", 81, 56, "OVER_LIMIT"),
    ("panel_thumbnails/sub/deeper/a.png", 34, 47, "OK"),
])
def test_each_folder_has_its_own_explicit_limit_and_it_covers_what_is_under_it(path, width, height, status):
    assert audit_mod.judge(path, width, height)[0] == status


@pytest.mark.parametrize("path", ["frames/a.png", "table_loss/a.png", "a.png", "home/a.png", "home/logs/a.png", "home/debug/live/a.png", "panel_thumbnails_extra/a.png", "xpanel_thumbnails/a.png"])
def test_an_image_anywhere_else_is_an_unexpected_location(path):
    assert audit_mod.judge(path, 50, 50)[0] == "UNEXPECTED_LOCATION"


@pytest.mark.parametrize("path", ["panel_thumbnails/a.png", "read_mismatches/a.png", "home/logs/failures/a.png", "frames/a.png", "anywhere/a.png"])
@pytest.mark.parametrize("width,height", [(400, 10), (10, 400), (1920, 1080), (400, 400)])
def test_a_side_of_400_pixels_fails_wherever_it_is(path, width, height):
    assert audit_mod.judge(path, width, height)[0] == "WHOLE_SCREEN"


def test_399_is_not_whole_screen_but_still_over_a_limit():
    assert audit_mod.judge("read_mismatches/a.png", 399, 399)[0] == "OVER_LIMIT" and audit_mod.judge("frames/a.png", 399, 399)[0] == "UNEXPECTED_LOCATION"


def test_the_limits_in_force_are_the_four_the_audit_proposed_and_the_threshold_is_400():
    assert {folder: (w, h) for folder, w, h in audit_mod.FOLDER_LIMITS} == {"panel_thumbnails": (120, 120), "read_mismatches": (220, 260),
                                                                           "home/logs/failures": (160, 160), "home/recognition/templates": (80, 80)}
    assert audit_mod.WHOLE_SCREEN_MIN == 400 and audit_mod.HEADER_LIMIT == 65536


def test_a_caller_can_pass_other_limits_and_they_are_obeyed():
    limits = (("frames", 2000, 2000),)
    assert audit_mod.judge("frames/a.png", 150, 150, limits=limits)[0] == "OK"
    assert audit_mod.judge("panel_thumbnails/a.png", 50, 50, limits=limits)[0] == "UNEXPECTED_LOCATION"
    assert audit_mod.judge("frames/a.png", 1500, 1500, limits=limits, whole_screen_min=1000)[0] == "WHOLE_SCREEN"


# -- the audit of a whole folder ------------------------------------------------------------------------------------------------------------------

def test_an_evidence_folder_with_only_permitted_images_passes(tmp_path):
    good_tree(tmp_path)
    report = audit_mod.audit(str(tmp_path))
    assert report["verdict"] == "PASS" and report["counts"] == {"OK": 5} and report["non_image_files"] == 3
    assert {item["path"] for item in report["images"]} == {"panel_thumbnails/0001_first_player0.png", "read_mismatches/round0_dealer_1_crop.png",
                                                          "read_mismatches/round0_dealer_1_context.png", "home/logs/failures/20261019-110000_dealer_1_unknown.png",
                                                          "home/recognition/templates/ranks/A.png"}
    assert audit_mod.main([str(tmp_path)]) == 0


def test_the_three_whole_screen_frames_of_the_earlier_run_would_fail_the_audit(tmp_path):
    good_tree(tmp_path)
    for name in ("round13_result-panel_t75.7.png", "round28_result-panel_t132.0.png", "round28_showdown-phase_t133.2.png"):
        put(tmp_path, "frames/" + name, png_bytes(1920, 1080))
    report = audit_mod.audit(str(tmp_path))
    assert report["verdict"] == "FAIL" and report["counts"]["WHOLE_SCREEN"] == 3
    assert [item["status"] for item in report["images"] if item["path"].startswith("frames/")] == ["WHOLE_SCREEN"] * 3
    assert audit_mod.main([str(tmp_path)]) == 2


def test_a_whole_screen_image_is_a_failure_even_inside_a_permitted_folder(tmp_path):
    good_tree(tmp_path)
    put(tmp_path, "panel_thumbnails/oops.png", png_bytes(1920, 1080))
    assert audit_mod.audit(str(tmp_path))["verdict"] == "FAIL"


def test_an_unexpected_place_an_oversized_crop_and_a_bad_file_each_need_review(tmp_path):
    for expected, relative, data in (("UNEXPECTED_LOCATION", "somewhere/new.png", png_bytes(10, 10)), ("OVER_LIMIT", "panel_thumbnails/big.png", png_bytes(150, 100)),
                                     ("UNREADABLE", "read_mismatches/broken.png", b"\x89PNG\r\n\x1a\n"), ("UNSUPPORTED", "read_mismatches/modern.webp", b"RIFF" + b"\x00" * 40)):
        root = tmp_path / ("case_" + expected)
        good_tree(str(root))
        put(str(root), relative, data)
        report = audit_mod.audit(str(root))
        assert report["verdict"] == "REVIEW" and report["counts"].get(expected) == 1, expected
        assert audit_mod.main([str(root)]) == 1


def test_an_image_by_signature_with_another_extension_is_flagged(tmp_path):
    good_tree(tmp_path)
    put(tmp_path, "home/notes.dat", png_bytes(40, 40))
    put(tmp_path, "home/plain.txt", b"just text that is not a picture")
    report = audit_mod.audit(str(tmp_path))
    flagged = [item for item in report["images"] if item["status"] != "OK"]
    assert [(item["path"], item["status"]) for item in flagged] == [("home/notes.dat", "EXTENSION_MISMATCH")] and report["verdict"] == "REVIEW"
    put(tmp_path, "home/screen.dat", png_bytes(1920, 1080))
    assert audit_mod.audit(str(tmp_path))["verdict"] == "FAIL"


def test_file_names_are_matched_case_insensitively_and_folders_by_whole_name(tmp_path):
    put(tmp_path, "PANEL_THUMBNAILS/A.PNG", png_bytes(34, 47))
    put(tmp_path, "panel_thumbnails2/a.png", png_bytes(34, 47))
    report = audit_mod.audit(str(tmp_path))
    assert {item["path"]: item["status"] for item in report["images"]} == {"PANEL_THUMBNAILS/A.PNG": "OK", "panel_thumbnails2/a.png": "UNEXPECTED_LOCATION"}


def test_a_missing_or_empty_folder_is_handled(tmp_path):
    assert audit_mod.main([str(tmp_path / "nope")]) == 3
    assert audit_mod.audit(str(tmp_path))["verdict"] == "PASS" and audit_mod.audit(str(tmp_path))["images"] == []


def test_a_symbolic_link_is_listed_and_not_followed(tmp_path):
    good_tree(tmp_path)
    outside = tmp_path.parent / (tmp_path.name + "_outside.png")
    outside.write_bytes(png_bytes(1920, 1080))
    try:
        os.symlink(str(outside), str(tmp_path / "panel_thumbnails" / "link.png"))
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links need privilege here")
    report = audit_mod.audit(str(tmp_path))
    assert report["symbolic_links_not_followed"] == ["panel_thumbnails/link.png"] and report["verdict"] == "PASS"


def test_a_symbolic_link_to_a_folder_is_listed_and_never_entered(tmp_path):
    evidence = tmp_path / "evidence"
    good_tree(str(evidence))
    outside = tmp_path / "elsewhere"
    put(str(outside), "huge/screen.png", png_bytes(1920, 1080))
    try:
        os.symlink(str(outside), str(evidence / "linked"), target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links need privilege here")
    report = audit_mod.audit(str(evidence))
    assert report["symbolic_links_not_followed"] == ["linked/"] and report["verdict"] == "PASS"
    assert not any("screen.png" in item["path"] for item in report["images"])


# -- it changes nothing ----------------------------------------------------------------------------------------------------------------------------

def test_auditing_moves_changes_deletes_and_adds_nothing_even_when_it_fails(tmp_path):
    good_tree(tmp_path)
    put(tmp_path, "frames/screen.png", png_bytes(1920, 1080))
    put(tmp_path, "somewhere/odd.png", png_bytes(20, 20))
    put(tmp_path, "read_mismatches/broken.png", b"\x89PNG")
    before = tree_state(tmp_path)
    audit_mod.audit(str(tmp_path))
    audit_mod.render(audit_mod.audit(str(tmp_path)), show_all=True)
    assert audit_mod.main([str(tmp_path), "--all"]) == 2
    assert tree_state(tmp_path) == before


def test_the_json_report_goes_outside_the_evidence_folder_and_never_inside_it(tmp_path):
    evidence = tmp_path / "evidence"
    good_tree(str(evidence))
    elsewhere = tmp_path / "reports" / "audit.json"
    elsewhere.parent.mkdir()
    before = tree_state(evidence)
    assert audit_mod.main([str(evidence), "--json", str(elsewhere)]) == 0
    assert json.load(open(elsewhere))["verdict"] == "PASS" and tree_state(evidence) == before
    inside = evidence / "audit.json"
    assert audit_mod.main([str(evidence), "--json", str(inside)]) == 3
    assert not inside.exists() and tree_state(evidence) == before
    assert audit_mod.main([str(evidence), "--json", str(evidence / "sub" / "x.json")]) == 3


def test_a_limit_can_be_replaced_or_added_on_the_command_line_and_a_bad_one_is_refused(tmp_path, capsys):
    good_tree(tmp_path)
    put(tmp_path, "panel_thumbnails/wide.png", png_bytes(150, 60))
    assert audit_mod.main([str(tmp_path)]) == 1
    assert audit_mod.main([str(tmp_path), "--limit", "panel_thumbnails=160x80"]) == 0
    put(tmp_path, "extra/a.png", png_bytes(30, 30))
    assert audit_mod.main([str(tmp_path), "--limit", "extra=40x40", "--limit", "panel_thumbnails=160x80"]) == 0
    assert audit_mod.main([str(tmp_path), "--limit", "nonsense"]) == 3


def test_the_text_report_lists_the_limits_the_flags_and_what_to_do_next(tmp_path, capsys):
    good_tree(tmp_path)
    put(tmp_path, "frames/screen.png", png_bytes(1920, 1080))
    assert audit_mod.main([str(tmp_path)]) == 2
    text = capsys.readouterr().out
    for expected in ("panel_thumbnails", "120 x 120", "220 x 260", "160 x 160", "80 x 80", "400 pixels on a side", "WHOLE_SCREEN", "frames/screen.png", "1920x1080",
                     "VERDICT: FAIL", "Nothing was moved, changed or deleted", "Do not share this folder", "headers only"):
        assert expected in text, expected


def test_a_passing_report_does_not_tell_anyone_to_stop(tmp_path, capsys):
    good_tree(tmp_path)
    assert audit_mod.main([str(tmp_path)]) == 0
    text = capsys.readouterr().out
    assert "VERDICT: PASS" in text and "Do not share" not in text


# -- the tool can only look ---------------------------------------------------------------------------------------------------------------------------

def test_the_audit_imports_and_calls_nothing_that_decodes_shows_moves_or_deletes_a_picture():
    tree = ast.parse(open(os.path.join(ROOT, "tools", "audit_evidence_images.py"), encoding="utf-8").read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported == {"argparse", "json", "os", "struct", "sys"}, imported
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    for banned in ("imread", "imdecode", "imshow", "Image", "cv2", "numpy", "PIL", "remove", "unlink", "rmdir", "rename", "renames", "rmtree", "move", "copy", "copyfile", "copy2",
                   "chmod", "utime", "truncate", "makedirs", "mkdir", "startfile", "system", "subprocess", "urlopen", "socket", "webbrowser", "environ"):
        assert banned not in names, banned


def test_every_open_in_the_audit_is_a_read_except_the_one_json_report():
    tree = ast.parse(open(os.path.join(ROOT, "tools", "audit_evidence_images.py"), encoding="utf-8").read())
    modes = [ast.literal_eval(node.args[1]) for node in ast.walk(tree) if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "open" and len(node.args) > 1]
    assert sorted(modes) == ["rb", "rb", "w"]
