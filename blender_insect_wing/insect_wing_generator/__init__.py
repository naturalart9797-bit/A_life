bl_info = {
    "name": "Insect Wing Generator",
    "author": "A_life",
    "version": (1, 1, 0),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar (N) > Insect Wing",
    "description": "Generate insect wings: Diptera (fly) wings from the "
                   "homologous vein plan, Odonata wings with the inhibition / "
                   "Voronoi model of Hoffmann et al. (PNAS 2018)",
    "category": "Add Mesh",
}

import random
import time

if "bpy" in locals():
    import importlib
    importlib.reload(venation)  # noqa: F821
    importlib.reload(diptera)   # noqa: F821
    importlib.reload(builder)   # noqa: F821
else:
    from . import venation
    from . import diptera
    from . import builder

import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty,
                       FloatVectorProperty, IntProperty, PointerProperty)


# ---------------------------------------------------------------------------
# Presets (UI level).  "fore" values map to venation.PRESETS, the "hind_*"
# values describe how the hind wing differs from the fore wing.
# ---------------------------------------------------------------------------

UI_PRESETS = {
    "DRAGONFLY": dict(
        fore=venation.PRESETS["DRAGONFLY_FORE"],
        hind=dict(hind_length=0.96, hind_chord=1.5, hind_base_power=0.16,
                  hind_extra_veins=2, hind_last_vein_end=0.04,
                  hind_subcosta_end=0.42)),
    "DAMSELFLY": dict(
        fore=venation.PRESETS["DAMSELFLY"],
        hind=dict(hind_length=0.97, hind_chord=1.0, hind_base_power=0.9,
                  hind_extra_veins=0, hind_last_vein_end=0.22,
                  hind_subcosta_end=0.25)),
    "LACEWING": dict(
        fore=venation.PRESETS["LACEWING"],
        hind=dict(hind_length=0.9, hind_chord=0.85, hind_base_power=0.4,
                  hind_extra_veins=-1, hind_last_vein_end=0.2,
                  hind_subcosta_end=0.8)),
    "MAYFLY": dict(
        fore=venation.PRESETS["MAYFLY"],
        hind=dict(hind_length=0.35, hind_chord=0.8, hind_base_power=0.3,
                  hind_extra_veins=-3, hind_last_vein_end=0.15,
                  hind_subcosta_end=0.7)),
}

# property name -> WingParams attribute (identical names)
WING_KEYS = [
    "chord", "base_power", "tip_power", "tip_drop", "le_bulge",
    "n_primary", "subcosta_end", "radius_end", "last_vein_end", "vein_spread",
    "branch_prob", "vein_curvature", "hinge",
    "intercalary_levels", "intercalary_threshold", "intercalary_stop",
    "resolution", "cell_size", "ladder_ratio", "min_cell_radius", "cell_noise",
    "pterostigma", "ptero_start", "ptero_end", "ptero_width",
]


FLY_KEYS = ["chord", "base_power", "tip_power", "tip_drop", "le_bulge",
            "alula", "alula_pos", "calypter", "r4_appendix"]

COLORS = {
    "DIPTERA": dict(membrane_color=(0.86, 0.86, 0.82), vein_color=(0.20, 0.09, 0.02),
                    membrane_alpha=0.22, iridescence=0.7),
    "ODONATA": dict(membrane_color=(0.75, 0.85, 0.9), vein_color=(0.05, 0.035, 0.02),
                    membrane_alpha=0.25, iridescence=1.0),
}


def _apply_family(self, _context):
    pr = diptera.PRESETS.get(self.fly_family)
    if pr is None:
        return
    defaults = diptera.FlyParams()
    for k in FLY_KEYS:
        setattr(self, "fly_" + k, pr.get(k, getattr(defaults, k)))


def _apply_model(self, _context):
    for k, v in COLORS[self.model].items():
        setattr(self, k, v)


def _apply_preset(self, _context):
    pr = UI_PRESETS.get(self.preset)
    if pr is None:
        return
    defaults = venation.WingParams()
    for k in WING_KEYS:
        setattr(self, k, pr["fore"].get(k, getattr(defaults, k)))
    for k, v in pr["hind"].items():
        setattr(self, k, v)


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

class IW_Settings(bpy.types.PropertyGroup):
    model: EnumProperty(
        name="Insect",
        items=[("DIPTERA", "Diptera (flies)",
                "One pair of wings with the conserved dipteran vein plan; "
                "hind wings reduced to halteres"),
               ("ODONATA", "Odonata / net-veined",
                "Dense venation grown by the inhibition / Voronoi model "
                "(Hoffmann et al., PNAS 2018)")],
        default="DIPTERA", update=_apply_model)

    # --- Diptera ---
    fly_family: EnumProperty(
        name="Family",
        items=[("TABANIDAE", "Horse fly (Tabanidae)",
                "Circumambient costa, R4/R5 fork with R4 appendix, closed "
                "discal cell, three M branches, cup closed at the margin"),
               ("MUSCIDAE", "House fly (Muscidae)",
                "Costa ends at M1, M1 bent forward toward R4+5, short cup"),
               ("SYRPHIDAE", "Hover fly (Syrphidae)",
                "Vena spuria, outer cross veins parallel to the margin")],
        default="TABANIDAE", update=_apply_family)
    fly_chord: FloatProperty(name="Chord", default=0.38, min=0.15, max=0.7)
    fly_base_power: FloatProperty(name="Base Taper", default=0.32, min=0.05, max=2.0)
    fly_tip_power: FloatProperty(name="Tip Taper", default=0.75, min=0.1, max=2.0)
    fly_tip_drop: FloatProperty(name="Apex Position", default=0.20, min=-0.2, max=0.8,
                                description="How far the apex lies behind the "
                                            "leading edge (x chord)")
    fly_le_bulge: FloatProperty(name="Leading Edge Bulge", default=0.0, min=-0.2, max=0.3)
    fly_alula: FloatProperty(name="Alula", default=0.08, min=0.0, max=0.4,
                             description="Size of the alula lobe at the posterior base")
    fly_alula_pos: FloatProperty(name="Alula Position", default=0.06, min=0.02, max=0.3)
    fly_calypter: FloatProperty(name="Calypters", default=0.08, min=0.0, max=0.4,
                                description="Size of the calypters (squamae)")
    fly_r4_appendix: BoolProperty(name="R4 Appendix", default=True,
                                  description="Short spur on R4 (Tabanidae)")
    fly_variation: FloatProperty(name="Individual Variation", default=0.3,
                                 min=0.0, max=2.0,
                                 description="Random displacement of vein junctions")
    fly_pigment: FloatProperty(name="Pigmentation", default=0.8, min=0.0, max=1.0,
                               description="Tint of the costal and basal cells")
    pigment_color: FloatVectorProperty(name="Pigment", subtype="COLOR",
                                       default=(0.72, 0.42, 0.10), min=0.0, max=1.0)
    halteres: BoolProperty(name="Halteres", default=True,
                           description="Add halteres (reduced hind wings)")
    haltere_color: FloatVectorProperty(name="Haltere", subtype="COLOR",
                                       default=(0.55, 0.38, 0.15), min=0.0, max=1.0)
    calypter_color: FloatVectorProperty(name="Calypter", subtype="COLOR",
                                        default=(0.85, 0.78, 0.62), min=0.0, max=1.0)
    show_fly: BoolProperty(default=True)

    preset: EnumProperty(
        name="Preset",
        items=[("DRAGONFLY", "Dragonfly", "Anisoptera: dense polygonal cells"),
               ("DAMSELFLY", "Damselfly", "Zygoptera: petiolate wings"),
               ("LACEWING", "Lacewing", "Neuroptera: ladder-like cross veins"),
               ("MAYFLY", "Mayfly", "Ephemeroptera: triangular fore wing"),
               ("CUSTOM", "Custom", "Keep current values")],
        default="DRAGONFLY", update=_apply_preset)

    layout_mode: EnumProperty(
        name="Wings",
        items=[("ONE", "Single Wing", "One wing only"),
               ("PAIR", "One Side", "Fore + hind wing (Odonata) or wing + "
                                    "haltere (Diptera) on the right side"),
               ("FOUR", "Both Sides", "Mirrored left and right")],
        default="FOUR")
    seed: IntProperty(name="Seed", default=1, min=0)
    span: FloatProperty(name="Wing Length", default=0.05, min=0.001,
                        unit="LENGTH", description="Length of the fore wing")
    body_gap: FloatProperty(name="Body Gap", default=0.06, min=0.0, max=1.0,
                            description="Distance of the wing hinge from the "
                                        "body axis (x wing length)")

    # --- outline ---
    chord: FloatProperty(name="Chord", default=0.2, min=0.03, max=0.8,
                         description="Max wing width (x wing length)")
    base_power: FloatProperty(name="Base Taper", default=0.45, min=0.05, max=2.0)
    tip_power: FloatProperty(name="Tip Taper", default=0.45, min=0.1, max=2.0)
    tip_drop: FloatProperty(name="Tip Drop", default=0.2, min=-0.5, max=1.0)
    le_bulge: FloatProperty(name="Leading Edge Bulge", default=0.03, min=-0.3, max=0.5)

    # --- longitudinal veins ---
    n_primary: IntProperty(name="Main Veins", default=8, min=2, max=24)
    subcosta_end: FloatProperty(name="Nodus Position", default=0.48, min=0.1, max=0.95)
    radius_end: FloatProperty(name="Radius End", default=0.95, min=0.5, max=0.995)
    last_vein_end: FloatProperty(name="Anal Vein End", default=0.1, min=0.01, max=0.9)
    vein_spread: FloatProperty(name="Vein Spread", default=1.0, min=0.3, max=3.0)
    branch_prob: FloatProperty(name="Branching", default=0.5, min=0.0, max=1.0)
    vein_curvature: FloatProperty(name="Vein Curvature", default=0.3, min=0.0, max=1.5)
    hinge: FloatProperty(name="Hinge Position", default=0.05, min=0.005, max=0.3)

    # --- intercalary veins ---
    intercalary_levels: IntProperty(name="Intercalary Levels", default=2, min=0, max=5)
    intercalary_threshold: FloatProperty(
        name="Gap Threshold", default=0.08, min=0.01, max=0.6,
        description="Gap between veins (x chord) above which an intercalary "
                    "vein forms along the line farthest from both neighbours")
    intercalary_stop: FloatProperty(name="Stop Ratio", default=0.55, min=0.1, max=1.0)

    # --- cross veins ---
    resolution: IntProperty(name="Resolution", default=450, min=100, max=1500,
                            description="Raster cells along the span used for "
                                        "the inhibition field (slower if higher)")
    cell_size: FloatProperty(name="Cell Size", default=0.09, min=0.01, max=0.5,
                             description="Largest cell radius in wide areas (x chord)")
    ladder_ratio: FloatProperty(name="Ladder Ratio", default=0.85, min=0.2, max=3.0,
                                description="Cross-vein spacing relative to the "
                                            "local gap between veins")
    min_cell_radius: FloatProperty(name="Min Cell (px)", default=1.2, min=0.75, max=10.0)
    cell_noise: FloatProperty(name="Irregularity", default=0.25, min=0.0, max=1.0)

    # --- pterostigma ---
    pterostigma: BoolProperty(name="Pterostigma", default=True)
    ptero_start: FloatProperty(name="Start", default=0.8, min=0.3, max=0.99)
    ptero_end: FloatProperty(name="End", default=0.88, min=0.3, max=0.995)
    ptero_width: FloatProperty(name="Width", default=0.1, min=0.01, max=0.5)

    # --- hind wing ---
    hind_length: FloatProperty(name="Length", default=0.96, min=0.1, max=2.0)
    hind_chord: FloatProperty(name="Chord Scale", default=1.5, min=0.3, max=3.0)
    hind_base_power: FloatProperty(name="Base Taper", default=0.16, min=0.05, max=2.0)
    hind_extra_veins: IntProperty(name="Extra Veins", default=2, min=-10, max=10)
    hind_last_vein_end: FloatProperty(name="Anal Vein End", default=0.04, min=0.01, max=0.9)
    hind_subcosta_end: FloatProperty(name="Nodus Position", default=0.42, min=0.1, max=0.95)

    # --- 3D shape and look ---
    camber: FloatProperty(name="Camber", default=0.06, min=-0.5, max=0.5)
    twist: FloatProperty(name="Twist", default=0.05, min=-0.5, max=0.5)
    vein_thickness: FloatProperty(name="Vein Thickness", default=0.002,
                                  min=0.0001, max=0.02, precision=4,
                                  description="Vein radius (x wing length)")
    vein_bevel_resolution: IntProperty(name="Vein Smoothness", default=1, min=0, max=6)
    veins_to_mesh: BoolProperty(name="Convert Veins to Mesh", default=False)
    membrane_color: FloatVectorProperty(name="Membrane", subtype="COLOR",
                                        default=(0.86, 0.86, 0.82), min=0.0, max=1.0)
    membrane_alpha: FloatProperty(name="Opacity", default=0.22, min=0.0, max=1.0)
    iridescence: FloatProperty(name="Iridescence", default=0.7, min=0.0, max=2.0)
    vein_color: FloatVectorProperty(name="Veins", subtype="COLOR",
                                    default=(0.20, 0.09, 0.02), min=0.0, max=1.0)
    stigma_color: FloatVectorProperty(name="Pterostigma", subtype="COLOR",
                                      default=(0.12, 0.05, 0.02), min=0.0, max=1.0)

    # UI fold states
    show_outline: BoolProperty(default=False)
    show_veins: BoolProperty(default=False)
    show_cells: BoolProperty(default=True)
    show_hind: BoolProperty(default=False)
    show_look: BoolProperty(default=False)


# ---------------------------------------------------------------------------
# Operator
# ---------------------------------------------------------------------------

def _fore_params(s, seed):
    kw = {k: getattr(s, k) for k in WING_KEYS}
    kw["seed"] = seed
    return venation.WingParams(**kw)


def _hind_params(s, seed):
    p = _fore_params(s, seed)
    p.length = s.hind_length
    p.chord = s.chord * s.hind_chord
    p.base_power = s.hind_base_power
    p.n_primary = max(2, s.n_primary + s.hind_extra_veins)
    p.last_vein_end = s.hind_last_vein_end
    p.subcosta_end = s.hind_subcosta_end
    p.resolution = max(100, int(s.resolution * s.hind_length))
    return p


def _fly_params(s, seed):
    kw = {k: getattr(s, "fly_" + k) for k in FLY_KEYS}
    return diptera.FlyParams(family=s.fly_family, variation=s.fly_variation,
                             pigment=s.fly_pigment, seed=seed, **kw)


def _to_mesh(obj, context):
    dg = context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(obj.evaluated_get(dg))
    new = bpy.data.objects.new(obj.name, me)
    for c in obj.users_collection:
        c.objects.link(new)
    new.parent = obj.parent
    new.matrix_parent_inverse = obj.matrix_parent_inverse.copy()
    curve = obj.data
    bpy.data.objects.remove(obj)
    bpy.data.curves.remove(curve)
    return new


class IW_OT_generate(bpy.types.Operator):
    """Generate insect wings with procedurally grown venation"""
    bl_idname = "mesh.insect_wing_generate"
    bl_label = "Generate Insect Wings"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = context.scene.insect_wing
        t0 = time.time()
        coll = bpy.data.collections.get("InsectWings")
        if coll is None:
            coll = bpy.data.collections.new("InsectWings")
            context.scene.collection.children.link(coll)

        fly = s.model == "DIPTERA"
        if fly:
            membrane = builder.fly_membrane_material(
                s.membrane_color, s.pigment_color, s.membrane_alpha, s.iridescence)
        else:
            membrane = builder.membrane_material(s.membrane_color, s.membrane_alpha,
                                                 s.iridescence)
        mats = {
            "membrane": membrane,
            "calypter": builder.solid_material("IW_Calypter", s.calypter_color, 0.6),
            "haltere": builder.solid_material("IW_Haltere", s.haltere_color, 0.5),
            "vein": builder.solid_material("IW_Vein", s.vein_color, 0.35),
            "stigma": builder.solid_material("IW_Pterostigma", s.stigma_color, 0.3),
        }

        root = bpy.data.objects.new("InsectWings", None)
        root.empty_display_type = "ARROWS"
        coll.objects.link(root)
        root.location = context.scene.cursor.location
        root.scale = (s.span, s.span, s.span)

        rng = random.Random(s.seed)
        fore_seed = rng.randrange(1 << 30)
        hind_seed = rng.randrange(1 << 30)
        if fly:
            jobs = [("Fly", _fly_params(s, fore_seed), 0.0)]
        else:
            jobs = [("Fore", _fore_params(s, fore_seed), 0.0)]
        if s.layout_mode in {"PAIR", "FOUR"} and not fly:
            jobs.append(("Hind", _hind_params(s, hind_seed), -s.chord * 1.25 - 0.02))
        sides = [("R", False)]
        if s.layout_mode == "FOUR":
            sides.append(("L", True))

        built = []
        for label, params, yoff in jobs:
            for side, mirror in sides:
                x = -s.body_gap if mirror else s.body_gap
                wing_root, res = builder.build_wing(
                    "%sWing_%s" % (label, side), params, s, coll, root,
                    (x, yoff, 0.0), mirror, mats)
                built.append((wing_root, res))
                if fly and s.halteres and s.layout_mode != "ONE":
                    builder.build_haltere("Haltere_%s" % side, params.length, mirror,
                                          mats["haltere"], coll, wing_root)

        if s.veins_to_mesh:
            context.view_layer.update()
            for wing_root, _res in built:
                for ch in list(wing_root.children):
                    if ch.type == "CURVE":
                        _to_mesh(ch, context)

        res = built[0][1]
        if fly:
            msg = "%s wing: %d named veins" % (s.fly_family.title(), len(res.labels))
        else:
            msg = "%d cross veins per fore wing" % sum(
                1 for v in res.veins if v.kind == "cross")
        self.report({"INFO"}, "Insect wings generated: %s (%.1fs)"
                    % (msg, time.time() - t0))
        return {"FINISHED"}


class IW_OT_randomize(bpy.types.Operator):
    """Pick a new random seed and regenerate"""
    bl_idname = "mesh.insect_wing_randomize"
    bl_label = "Random Seed"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        context.scene.insect_wing.seed = random.randrange(100000)
        return bpy.ops.mesh.insect_wing_generate()


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

def _fold(layout, s, prop, title):
    box = layout.box()
    row = box.row()
    open_ = getattr(s, prop)
    row.prop(s, prop, text=title, emboss=False,
             icon="TRIA_DOWN" if open_ else "TRIA_RIGHT")
    return box if open_ else None


class IW_PT_panel(bpy.types.Panel):
    bl_label = "Insect Wing"
    bl_idname = "IW_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Insect Wing"

    def draw(self, context):
        s = context.scene.insect_wing
        lay = self.layout
        lay.prop(s, "model")
        fly = s.model == "DIPTERA"
        if fly:
            lay.prop(s, "fly_family")
        else:
            lay.prop(s, "preset")
        lay.prop(s, "layout_mode")
        row = lay.row(align=True)
        row.prop(s, "seed")
        row.operator("mesh.insect_wing_randomize", text="", icon="FILE_REFRESH")
        lay.prop(s, "span")
        lay.prop(s, "body_gap")

        if fly:
            b = _fold(lay, s, "show_fly", "Fly Wing")
            if b:
                for k in ("fly_chord", "fly_base_power", "fly_tip_power",
                          "fly_tip_drop", "fly_le_bulge", "fly_alula",
                          "fly_alula_pos", "fly_calypter", "fly_r4_appendix",
                          "fly_variation", "fly_pigment", "halteres"):
                    b.prop(s, k)
            b = _fold(lay, s, "show_look", "Shape & Material")
            if b:
                for k in ("camber", "twist", "vein_thickness", "vein_bevel_resolution",
                          "veins_to_mesh", "membrane_color", "pigment_color",
                          "membrane_alpha", "iridescence", "vein_color",
                          "calypter_color", "haltere_color"):
                    b.prop(s, k)
            lay.separator()
            lay.operator("mesh.insect_wing_generate", icon="MOD_WIREFRAME")
            return

        b = _fold(lay, s, "show_outline", "Outline")
        if b:
            for k in ("chord", "base_power", "tip_power", "tip_drop", "le_bulge"):
                b.prop(s, k)

        b = _fold(lay, s, "show_veins", "Longitudinal Veins")
        if b:
            for k in ("n_primary", "subcosta_end", "radius_end", "last_vein_end",
                      "vein_spread", "branch_prob", "vein_curvature", "hinge"):
                b.prop(s, k)
            b.label(text="Intercalary veins (farthest from neighbours):")
            for k in ("intercalary_levels", "intercalary_threshold", "intercalary_stop"):
                b.prop(s, k)

        b = _fold(lay, s, "show_cells", "Cross Veins / Cells")
        if b:
            for k in ("cell_size", "ladder_ratio", "cell_noise", "min_cell_radius",
                      "resolution"):
                b.prop(s, k)
            b.prop(s, "pterostigma")
            if s.pterostigma:
                r = b.row(align=True)
                r.prop(s, "ptero_start")
                r.prop(s, "ptero_end")
                b.prop(s, "ptero_width")

        b = _fold(lay, s, "show_hind", "Hind Wing")
        if b:
            for k in ("hind_length", "hind_chord", "hind_base_power",
                      "hind_extra_veins", "hind_last_vein_end", "hind_subcosta_end"):
                b.prop(s, k)

        b = _fold(lay, s, "show_look", "Shape & Material")
        if b:
            for k in ("camber", "twist", "vein_thickness", "vein_bevel_resolution",
                      "veins_to_mesh", "membrane_color", "membrane_alpha",
                      "iridescence", "vein_color", "stigma_color"):
                b.prop(s, k)

        lay.separator()
        lay.operator("mesh.insect_wing_generate", icon="MOD_WIREFRAME")


def menu_func(self, _context):
    self.layout.operator(IW_OT_generate.bl_idname, text="Insect Wings",
                         icon="MOD_WIREFRAME")


classes = (IW_Settings, IW_OT_generate, IW_OT_randomize, IW_PT_panel)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.insect_wing = PointerProperty(type=IW_Settings)
    bpy.types.VIEW3D_MT_mesh_add.append(menu_func)


def unregister():
    bpy.types.VIEW3D_MT_mesh_add.remove(menu_func)
    del bpy.types.Scene.insect_wing
    for c in reversed(classes):
        bpy.utils.unregister_class(c)


if __name__ == "__main__":
    register()
