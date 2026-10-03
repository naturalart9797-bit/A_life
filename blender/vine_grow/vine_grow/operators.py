import bpy
from bpy.props import FloatProperty
from bpy_extras import view3d_utils
from mathutils import Vector

from . import binding, pipeline
from .sampler import BodySampler


def _target(op, context):
    t = pipeline.get_target(context)
    if t is None:
        op.report({"ERROR"}, "対象オブジェクトを設定してください")
    return t


class VINEGROW_OT_set_target(bpy.types.Operator):
    bl_idname = "vine_grow.set_target"
    bl_label = "選択中を対象に設定"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = context.active_object
        if obj is None or obj.type != "MESH":
            self.report({"ERROR"}, "メッシュオブジェクトを選択してください")
            return {"CANCELLED"}
        context.scene.vine_grow.target = obj
        return {"FINISHED"}


class VINEGROW_OT_generate(bpy.types.Operator):
    bl_idname = "vine_grow.generate"
    bl_label = "つるを生成"
    bl_description = "起点から対象に絡みつくように、つるを伸ばす"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        target = _target(self, context)
        if target is None:
            return {"CANCELLED"}
        return {"FINISHED"} if pipeline.run(context, target, self.report) else {"CANCELLED"}


class VINEGROW_OT_clear(bpy.types.Operator):
    bl_idname = "vine_grow.clear"
    bl_label = "つるを削除"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        target = _target(self, context)
        if target is None:
            return {"CANCELLED"}
        pipeline.remove_network(target)
        return {"FINISHED"}


_NAV = {"MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE", "WHEELINMOUSE", "WHEELOUTMOUSE",
        "TRACKPADPAN", "TRACKPADZOOM", "MOUSEROTATE", "MOUSESMARTZOOM"}


class VINEGROW_OT_place_origin(bpy.types.Operator):
    bl_idname = "vine_grow.place_origin"
    bl_label = "起点を置く"
    bl_description = "対象の上をクリックして起点を置く（続けてクリックで複数。Enter/右クリック/Escで終了）"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == "VIEW_3D"

    def invoke(self, context, event):
        target = _target(self, context)
        if target is None:
            return {"CANCELLED"}
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for arm in binding.target_armatures(target):
            arm.data.pose_position = "REST"
        context.view_layer.update()
        self.target = target
        self.sampler = BodySampler(context, target)
        self.placed = 0
        self.region = None
        context.area.header_text_set("クリック: 起点を置く　Enter/右クリック/Esc: 終了　Alt+ドラッグ・中ボタン: 視点操作")
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def _end(self, context):
        context.area.header_text_set(None)

    def modal(self, context, event):
        if event.alt or event.oskey or event.type in _NAV or event.type.startswith("NDOF"):
            return {"PASS_THROUGH"}
        if event.type in {"RET", "NUMPAD_ENTER", "ESC", "RIGHTMOUSE"} and event.value == "PRESS":
            self._end(context)
            return {"FINISHED"} if self.placed else {"CANCELLED"}
        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            region, rv3d = context.region, context.region_data
            if region is None or rv3d is None:
                return {"PASS_THROUGH"}
            m = Vector((event.mouse_region_x, event.mouse_region_y))
            if not (0 <= m.x < region.width and 0 <= m.y < region.height):
                return {"PASS_THROUGH"}
            o = view3d_utils.region_2d_to_origin_3d(region, rv3d, m)
            v = view3d_utils.region_2d_to_vector_3d(region, rv3d, m)
            hit = self.sampler.ray_cast(o, v)
            if hit is None:
                return {"RUNNING_MODAL"}
            P = context.scene.vine_grow
            base = P.default_width if P.mode == "ROUTE" else P.default_reach
            reach = base * pipeline.target_scale(self.target, P)
            org = pipeline.add_origin(context, self.target, hit.loc, reach)
            for ob in context.selected_objects:
                ob.select_set(False)
            org.select_set(True)
            context.view_layer.objects.active = org
            self.placed += 1
            context.area.tag_redraw()
            return {"RUNNING_MODAL"}
        return {"PASS_THROUGH"}


class VINEGROW_OT_origin_at_cursor(bpy.types.Operator):
    bl_idname = "vine_grow.origin_at_cursor"
    bl_label = "3Dカーソルに起点"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        target = _target(self, context)
        if target is None:
            return {"CANCELLED"}
        P = context.scene.vine_grow
        base = P.default_width if P.mode == "ROUTE" else P.default_reach
        pipeline.add_origin(context, target, context.scene.cursor.location.copy(),
                            base * pipeline.target_scale(target, P))
        return {"FINISHED"}


class VINEGROW_OT_origin_remove(bpy.types.Operator):
    bl_idname = "vine_grow.origin_remove"
    bl_label = "起点を削除"
    bl_options = {"REGISTER", "UNDO"}

    name: bpy.props.StringProperty()

    def execute(self, context):
        o = bpy.data.objects.get(self.name)
        if o is not None:
            bpy.data.objects.remove(o, do_unlink=True)
        return {"FINISHED"}


class VINEGROW_OT_origin_select(bpy.types.Operator):
    bl_idname = "vine_grow.origin_select"
    bl_label = "起点を選択"
    bl_options = {"REGISTER", "UNDO"}

    name: bpy.props.StringProperty()

    def execute(self, context):
        o = bpy.data.objects.get(self.name)
        if o is None or o.name not in context.view_layer.objects:
            return {"CANCELLED"}
        for ob in context.selected_objects:
            ob.select_set(False)
        o.select_set(True)
        context.view_layer.objects.active = o
        return {"FINISHED"}


class VINEGROW_OT_origin_move(bpy.types.Operator):
    bl_idname = "vine_grow.origin_move"
    bl_label = "経由点の順番を変える"
    bl_options = {"REGISTER", "UNDO"}

    name: bpy.props.StringProperty()
    delta: bpy.props.IntProperty(default=-1)

    def execute(self, context):
        target = _target(self, context)
        o = bpy.data.objects.get(self.name)
        if target is None or o is None:
            return {"CANCELLED"}
        objs = pipeline.origin_objects(target)
        i = objs.index(o)
        j = i + self.delta
        if not (0 <= j < len(objs)):
            return {"CANCELLED"}
        objs[i], objs[j] = objs[j], objs[i]
        for k, ob in enumerate(objs):
            ob["vine_grow_order"] = k
        return {"FINISHED"}


class VINEGROW_OT_toggle_rest(bpy.types.Operator):
    bl_idname = "vine_grow.toggle_rest"
    bl_label = "レスト/ポーズ切替"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        target = _target(self, context)
        if target is None:
            return {"CANCELLED"}
        for arm in binding.target_armatures(target):
            arm.data.pose_position = "POSE" if arm.data.pose_position == "REST" else "REST"
        return {"FINISHED"}


classes = (
    VINEGROW_OT_set_target,
    VINEGROW_OT_generate,
    VINEGROW_OT_clear,
    VINEGROW_OT_place_origin,
    VINEGROW_OT_origin_at_cursor,
    VINEGROW_OT_origin_remove,
    VINEGROW_OT_origin_select,
    VINEGROW_OT_origin_move,
    VINEGROW_OT_toggle_rest,
)
