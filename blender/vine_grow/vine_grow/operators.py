import bpy

from . import binding, pipeline


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
    bl_description = "塗った密度に合わせて、対象に絡みつくつるを生やす"
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
    VINEGROW_OT_toggle_rest,
)
