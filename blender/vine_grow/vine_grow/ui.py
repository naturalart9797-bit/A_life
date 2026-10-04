import bpy

from . import pipeline, points


class _Base:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Vine Grow"


def _wrap(text, n):
    return [text[i:i + n] for i in range(0, len(text), n)] or [""]


def _cols(layout, data, *groups):
    for names in groups:
        col = layout.column(align=True)
        for name in names:
            col.prop(data, name)


def _eye(row, P, name, text):
    row.prop(P, name, text=text, icon="HIDE_OFF" if getattr(P, name) else "HIDE_ON", toggle=True)


class _Targeted:
    @classmethod
    def poll(cls, context):
        return context.scene.vine_grow.target is not None


class VINEGROW_PT_main(_Base, bpy.types.Panel):
    bl_label = "つる植物"

    def draw(self, context):
        P = context.scene.vine_grow
        layout = self.layout
        row = layout.row(align=True)
        row.prop(P, "target")
        row.operator("vine_grow.set_target", text="", icon="EYEDROPPER")
        if P.target is None:
            layout.label(text="つるを生やすメッシュを選んでください", icon="INFO")
            return
        layout.prop(P, "density")
        row = layout.row()
        row.scale_y = 1.5
        row.operator("vine_grow.generate", icon="PLAY")
        if P.last_message:
            box = layout.box()
            box.alert = not P.last_ok
            for line in _wrap(P.last_message, 22):
                box.label(text=line, icon="NONE")
        row = layout.row(align=True)
        row.operator("vine_grow.toggle_rest", icon="ARMATURE_DATA")
        row.operator("vine_grow.clear", text="", icon="TRASH")
        row = layout.row(align=True)
        _eye(row, P, "show_points", "点")
        _eye(row, P, "show_vines", "つる")
        _eye(row, P, "show_leaves", "葉")
        nets = pipeline.network_objects(P.target)
        if nets:
            layout.prop(P, "growth", slider=True)
            node = pipeline.growth_node(nets[0])
            if node is not None:
                layout.prop(node.outputs[0], "default_value", text="成長（キーフレーム用）", slider=True)


class VINEGROW_PT_points(_Base, _Targeted, bpy.types.Panel):
    bl_label = "点・ペイント"

    def draw(self, context):
        """Density points (Yeti-style): scatter, then paint the value (0..100) with the brush."""
        P = context.scene.vine_grow
        layout = self.layout
        ps = points.get(P.target)
        n = len(ps) if ps else 0
        col = layout.column(align=True)
        col.operator("vine_grow.points_scatter", icon="PARTICLES")
        col.prop(P, "point_spacing")
        col.prop(P, "point_default")
        box = layout.box()
        box.row(align=True).prop(P, "brush_tool", expand=True)
        row = box.row()
        row.scale_y = 1.4
        row.operator("vine_grow.brush", text="ブラシで塗る", icon="BRUSH_DATA")
        c = box.column(align=True)
        c.prop(P, "brush_value", slider=True)
        c.prop(P, "brush_radius")
        c.prop(P, "brush_strength", slider=True)
        if P.brush_tool == "ADD":
            box.prop(P, "add_with_value")
        box.label(text="Ctrl: 減らす/削除  Shift: ぼかす", icon="INFO")
        box.label(text="B を押したまま上下: ブラシの大きさ")
        layout.label(text="色: 紫=0 → 青 → 水色 → 緑=100")
        c = layout.column(align=True)
        c.prop(P, "point_size")
        c.prop(P, "point_blend")
        row = layout.row(align=True)
        row.label(text="点 %d 個 / 塗った点 %d 個" % (n, sum(1 for v in ps.val if v > 0.0) if ps else 0))
        row.operator("vine_grow.points_fill", text="", icon="SNAP_FACE")
        row.operator("vine_grow.points_clear", text="", icon="TRASH")


class VINEGROW_PT_network(_Base, _Targeted, bpy.types.Panel):
    bl_label = "つるの広がり"

    def draw(self, context):
        _cols(self.layout, context.scene.vine_grow, ("contrast", "containment"),
              ("tangle_length", "curl", "curl_length"),
              ("internode", "fine_branch", "tendril_chance"),
              ("spread", "cling", "fine_aerial", "aerial_lift", "clearance"),
              ("fine_step", "max_nodes", "seed"))


class VINEGROW_PT_look(_Base, _Targeted, bpy.types.Panel):
    bl_label = "太さ・見た目"

    def draw(self, context):
        _cols(self.layout, context.scene.vine_grow, ("fine_r_min", "fine_r_max", "ring_res"),
              ("color_thin", "color_thick", "roughness", "subsurface", "bump"))


class VINEGROW_PT_leaves(_Base, _Targeted, bpy.types.Panel):
    bl_label = "葉"

    def draw(self, context):
        P = context.scene.vine_grow
        layout = self.layout
        _eye(layout, P, "show_leaves", "葉を表示")
        _cols(layout, P, ("leaf_chance", "leaf_spacing"),
              ("leaf_size", "leaf_size_var", "leaf_tip_small"),
              ("leaf_width", "leaf_lobe", "leaf_point", "leaf_petiole"),
              ("leaf_cup", "leaf_fold", "leaf_droop", "leaf_face"),
              ("leaf_color", "leaf_color_young"))
        _cols(layout, P, ("leaf_translucency", "leaf_roughness"))
        layout.label(text="形・付き方を変えたら「つるを生成」し直す", icon="INFO")


class VINEGROW_PT_settings(_Base, _Targeted, bpy.types.Panel):
    bl_label = "追従・設定"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        P = context.scene.vine_grow
        self.layout.prop(P, "bind_mode")
        _cols(self.layout, P, ("auto_scale", "rest_pose"))


classes = (VINEGROW_PT_main, VINEGROW_PT_points, VINEGROW_PT_network, VINEGROW_PT_look, VINEGROW_PT_leaves,
           VINEGROW_PT_settings)
