"""Blender: play a Conductor "Export for 3D simulator" file on a 3D model.

Reads an epaper-3d-timeline JSON (docs/EXPORT_3D_FORMAT.md) and keys every
scale object's colour exactly as the Conductor's Timeline shows it - the
sweep and the repaint flicker included - with CONSTANT interpolation.

The one agreement with the model: each scale is an object named by its map
label ("017-01" = board 17, socket 1). Labels are unique across the whole
show, so no item prefix is needed; "<item>_<label>" and "<item>/<label>" are
accepted too.

The colour goes on the OBJECT (Object Properties > Viewport Display >
Color, `obj.color`), not on a material, so one shared material serves every
scale:
  - viewport: Solid shading > Color: Object shows it at once;
  - render: the material "EPaper Scale" this script creates reads it through
    an Object Info node (Color). Set ASSIGN_MATERIAL to put it on every
    scale, or add Object Info > Color to your own material.

Use - either:
  a) Blender's Scripting tab: open this file, set JSON_PATH below, Run Script;
  b) command line:
     blender model.blend --python import_epaper_3d.py -- show-3d.json

Run it again after a new export: the previous keys are replaced.
Tested against Blender 3.6 - 4.x (legacy actions) and 4.4+/5.x (slotted
actions); both APIs are handled below.
"""

import json
import sys

try:
    import bpy
except ImportError:                     # outside Blender: the helpers stay importable
    bpy = None

# ---- settings ----------------------------------------------------------------
JSON_PATH = r""            # the exported file; or pass it after "--" on the command line
ITEMS = None               # None = every item; or e.g. ["AZ271SD1307", "AZ271SD1305"]
INCLUDE_PRESET = True      # True: the frame range starts at the first preset key (t < 0)
NULL_COLOR = "#808080"     # a scale with no show colour (before its first design)
ASSIGN_MATERIAL = False    # True: put the shared "EPaper Scale" material on every scale
EMISSION = 0.0             # >0 adds that much emission of the scale colour (e-paper does not glow)
MUSIC_PATH = r""           # optional: the show's audio file, placed so it starts at t = 0
SNAP_TO_FRAMES = False     # True: one key per frame (last colour wins) - lighter, loses sub-frame flicker
ACTION_PREFIX = "EPaper "  # actions are named ACTION_PREFIX + label (replaced on re-run)
# -------------------------------------------------------------------------------


def srgb_to_linear(c: float) -> float:
    """One sRGB channel (0..1) to scene linear, as Blender stores colours."""
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def hex_to_rgba(hex_color: str) -> "tuple[float, float, float, float]":
    h = hex_color.lstrip("#")
    return tuple(srgb_to_linear(int(h[i:i + 2], 16) / 255) for i in (0, 2, 4)) + (1.0,)


def frame_of(t: float, fps: float, offset_s: float, frame_start: int) -> float:
    """The (possibly fractional) frame of show time t."""
    return frame_start + (t + offset_s) * fps


def track_frames(track, fps, offset_s, frame_start, snap=False):
    """[(frame, colour index or None)] for one scale's keys, strictly increasing
    in frame. With `snap`, keys landing on the same whole frame collapse to the
    last one (what that frame would show at its end)."""
    out = []
    for t, index in track:
        f = frame_of(t, fps, offset_s, frame_start)
        if snap:
            f = float(round(f))
            if out and out[-1][0] == f:
                out[-1] = (f, index)
                continue
        out.append((f, index))
    return out


def find_object(objects, item: str, label: str):
    for name in (label, f"{item}_{label}", f"{item}/{label}"):
        obj = objects.get(name)
        if obj is not None:
            return obj
    return None


def _color_fcurves(obj, action_name: str):
    """Four fresh F-curves for obj.color[0..3] in a new action on obj, through
    the slotted-action API (Blender 4.4+, the only one in 5.x) or the legacy
    action.fcurves (older Blender)."""
    old = bpy.data.actions.get(action_name)
    if old is not None:
        bpy.data.actions.remove(old)
    action = bpy.data.actions.new(action_name)
    action.use_fake_user = False
    obj.animation_data_create()
    if hasattr(action, "layers"):                       # slotted actions
        slot = action.slots.new(id_type="OBJECT", name=obj.name)
        layer = action.layers.new("Layer")
        strip = layer.strips.new(type="KEYFRAME")
        bag = strip.channelbag(slot, ensure=True)
        curves = [bag.fcurves.new("color", index=i) for i in range(4)]
        obj.animation_data.action = action
        obj.animation_data.action_slot = slot
    else:                                               # legacy
        curves = [action.fcurves.new("color", index=i) for i in range(4)]
        obj.animation_data.action = action
    return curves


def _key_scale(obj, frames, rgba_of, action_name):
    curves = _color_fcurves(obj, action_name)
    n = len(frames)
    for ch, fc in enumerate(curves):
        fc.keyframe_points.add(n)
        co = []
        for f, index in frames:
            co += (f, rgba_of(index)[ch])
        fc.keyframe_points.foreach_set("co", co)
        # CONSTANT = 0 in the interpolation enum: hold each colour to the next key.
        fc.keyframe_points.foreach_set("interpolation", [0] * n)
        fc.update()


def _shared_material():
    mat = bpy.data.materials.get("EPaper Scale")
    if mat is not None:
        return mat
    mat = bpy.data.materials.new("EPaper Scale")
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = next(n for n in nodes if n.type == "BSDF_PRINCIPLED")
    info = nodes.new("ShaderNodeObjectInfo")
    info.location = (bsdf.location.x - 300, bsdf.location.y)
    links.new(info.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 0.6
    if EMISSION > 0:
        emit = bsdf.inputs.get("Emission Color") or bsdf.inputs.get("Emission")
        links.new(info.outputs["Color"], emit)
        bsdf.inputs["Emission Strength"].default_value = EMISSION
    return mat


def _place_music(scene, path, frame):
    scene.sequence_editor_create()
    seq = scene.sequence_editor
    strips = getattr(seq, "strips", None) or seq.sequences    # 4.4+ / older
    for s in [s for s in strips if s.name == "EPaper music"]:
        strips.remove(s)
    strip = strips.new_sound("EPaper music", path, channel=1, frame_start=int(round(frame)))
    return strip


def import_show(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if data.get("format") != "epaper-3d-timeline" or data.get("version") != 1:
        raise ValueError(f"{path} is not an epaper-3d-timeline v1 file")

    scene = bpy.context.scene
    fps = scene.render.fps / scene.render.fps_base
    items = {k: v for k, v in data["items"].items() if ITEMS is None or k in ITEMS}
    first = min((track[0][0] for item in items.values()
                 for track in item["keys"].values() if track), default=0.0)
    offset_s = -min(first, 0.0) if INCLUDE_PRESET else 0.0
    frame_start = scene.frame_start

    table = [hex_to_rgba(h) for h in data["colors"]]
    null_rgba = hex_to_rgba(NULL_COLOR)

    def rgba_of(index):
        return null_rgba if index is None else table[index]

    material = _shared_material()
    keyed, missing, keys = 0, [], 0
    for item_name, item in items.items():
        for label, track in item["keys"].items():
            obj = find_object(bpy.data.objects, item_name, label)
            if obj is None:
                missing.append(f"{item_name} {label}")
                continue
            frames = track_frames(track, fps, offset_s, frame_start, SNAP_TO_FRAMES)
            if not frames:
                continue
            obj.color = rgba_of(None)
            _key_scale(obj, frames, rgba_of, ACTION_PREFIX + label)
            if ASSIGN_MATERIAL and getattr(obj, "data", None) is not None \
                    and hasattr(obj.data, "materials"):
                obj.data.materials.clear()
                obj.data.materials.append(material)
            keyed += 1
            keys += len(frames)

    scene.frame_end = int(round(frame_of(data["duration"], fps, offset_s, frame_start)))
    if MUSIC_PATH:
        _place_music(scene, MUSIC_PATH, frame_of(0.0, fps, offset_s, frame_start))
    report = {"scales keyed": keyed, "keys": keys, "missing objects": len(missing),
              "show 0:00 at frame": round(frame_of(0.0, fps, offset_s, frame_start), 2),
              "frames": (scene.frame_start, scene.frame_end), "fps": fps}
    print("[epaper-3d]", report)
    if missing:
        print("[epaper-3d] no object for:", ", ".join(missing[:40]),
              "..." if len(missing) > 40 else "")
    return report


def _path_from_argv() -> str:
    argv = sys.argv
    return argv[argv.index("--") + 1] if "--" in argv and argv.index("--") + 1 < len(argv) else ""


if __name__ == "__main__" and bpy is not None:
    import_show(_path_from_argv() or JSON_PATH)
