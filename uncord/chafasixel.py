#turns pil images into sixel with libchafa via ctypes falls back to textual-image if chafa is missing
from __future__ import annotations

import ctypes
import logging
import os
from typing import Optional

log = logging.getLogger("uncord")

_lib = None
_glib = None
_term = None

PIXEL_RGBA8_UNASSOCIATED = 4
PIXEL_RGB8 = 8
PIXEL_MODE_SIXELS = 1
DITHER_NONE, DITHER_ORDERED, DITHER_DIFFUSION = 0, 1, 2


#glib GString the struct chafa returns its output in
class _GString(ctypes.Structure):
    _fields_ = [("str", ctypes.c_char_p), ("len", ctypes.c_size_t), ("allocated_len", ctypes.c_size_t)]


def _load() -> bool:
    global _lib, _glib, _term
    if _lib is not None:
        return _lib is not False
    from .deps import CHAFA

    for path in (CHAFA,):
        if not path:
            continue
        try:
            lib = ctypes.CDLL(path)
            break
        except OSError:
            continue
    else:
        _lib = False
        return False
    try:
        glib = ctypes.CDLL("libglib-2.0.so.0")
        vp, i = ctypes.c_void_p, ctypes.c_int
        for name, res, args in [
            ("chafa_canvas_config_new", vp, []),
            ("chafa_canvas_config_unref", None, [vp]),
            ("chafa_canvas_config_set_geometry", None, [vp, i, i]),
            ("chafa_canvas_config_set_cell_geometry", None, [vp, i, i]),
            ("chafa_canvas_config_set_pixel_mode", None, [vp, i]),
            ("chafa_canvas_config_set_dither_mode", None, [vp, i]),
            ("chafa_canvas_config_set_transparency_threshold", None, [vp, ctypes.c_float]),
            ("chafa_canvas_new", vp, [vp]),
            ("chafa_canvas_unref", None, [vp]),
            ("chafa_canvas_draw_all_pixels", None, [vp, i, ctypes.c_char_p, i, i, i]),
            ("chafa_canvas_print", ctypes.POINTER(_GString), [vp, vp]),
            ("chafa_term_db_get_default", vp, []),
            ("chafa_term_db_detect", vp, [vp, ctypes.POINTER(ctypes.c_char_p)]),
            ("chafa_set_n_threads", None, [i]),
        ]:
            fn = getattr(lib, name)
            fn.restype = res
            fn.argtypes = args
        glib.g_string_free.restype = vp
        glib.g_string_free.argtypes = [ctypes.POINTER(_GString), ctypes.c_int]
        env = (ctypes.c_char_p * 2)(b"TERM=foot", None)  #any sixel terminal works
        _term = lib.chafa_term_db_detect(lib.chafa_term_db_get_default(), env)
        lib.chafa_set_n_threads(max(1, min(4, (os.cpu_count() or 2) // 4)))
    except Exception:
        log.warning("libchafa unusable", exc_info=True)
        _lib = False
        return False
    _lib, _glib = lib, glib
    return True


#an all background sixel means encoding went wrong
from . import native  # noqa: E402

hasPixels = native.hasPixels


def available() -> bool:
    return _load()


def encode(
    img, cols: int, rows: int, cellW: int, cellH: int, dither: int = DITHER_DIFFUSION
) -> Optional[str]:
    if not _load():
        return None
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA" if "A" in img.getbands() else "RGB")
    w, h = img.size
    rgba = img.mode == "RGBA"
    cfg = _lib.chafa_canvas_config_new()
    try:
        _lib.chafa_canvas_config_set_pixel_mode(cfg, PIXEL_MODE_SIXELS)
        _lib.chafa_canvas_config_set_geometry(cfg, cols, rows)
        _lib.chafa_canvas_config_set_cell_geometry(cfg, cellW, cellH)
        _lib.chafa_canvas_config_set_dither_mode(cfg, dither)
        if rgba:  #threshold 0 makes everything transparent
            _lib.chafa_canvas_config_set_transparency_threshold(cfg, 0.5)
        canvas = _lib.chafa_canvas_new(cfg)
        try:
            _lib.chafa_canvas_draw_all_pixels(
                canvas,
                PIXEL_RGBA8_UNASSOCIATED if rgba else PIXEL_RGB8,
                img.tobytes(),
                w,
                h,
                w * (4 if rgba else 3),
            )
            gs = _lib.chafa_canvas_print(canvas, _term)
            try:
                return ctypes.string_at(gs.contents.str, gs.contents.len).decode("ascii", "replace")
            finally:
                _glib.g_string_free(gs, 1)
        finally:
            _lib.chafa_canvas_unref(canvas)
    finally:
        _lib.chafa_canvas_config_unref(cfg)
