import traceback

import bpy
from bpy.props import EnumProperty, StringProperty

from . import binding, groom_tree, guides, pipeline
from .sampler import BodySampler
from .tools import GroupEdit, Snapper, active_group


def _need_body(op, context):
    body = pipeline.get_body(context)
    if body is None:
        op.report({"ERROR"}, "人物メッシュを指定してください")
    elif context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    return body


def _set_active(context, obj):
    context.scene.vine_dress.active_group_index = list(bpy.data.objects).index(obj)


# ======================================================================
# Generation
# ======================================================================
class VINEDRESS_OT_generate_guides(bpy.types.Operator):
    bl_idname = "vine_dress.generate_guides"
    bl_label = "ガイドを自動生成"
    bl_description = ("体に吸着した上半身ガイドとスカートガイドを自動で作る（グループ VG_Body / VG_Skirt を作り直す。"
                      "他のグループは変更しない）")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        body = _need_body(self, context)
        if body is None:
            return {"CANCELLED"}
        try:
            n = pipeline.make_guides(self.report, context, context.scene.vine_dress, body)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self.report({"ERROR"}, "ガイド生成に失敗しました: %s" % exc)
            return {"CANCELLED"}
        self.report({"INFO"}, "ガイド %d 本を生成しました" % n)
        groom_tree.schedule_for_body(body)
        return {"FINISHED"}


def _tree_for(context, body, name=""):
    tree = bpy.data.node_groups.get(name) if name else None
    if tree is None:
        tree = groom_tree.ensure_tree(context, body)
        if not any(n.bl_idname == "VineNodeGroupInput" for n in tree.nodes):
            for g in guides.guide_objects(body):
                groom_tree.tree_add_group(tree, g)
    return tree


class VINEDRESS_OT_build(bpy.types.Operator):
    bl_idname = "vine_dress.build"
    bl_label = "つるを生成"
    bl_description = "グルームツリーを評価して、つる・葉・巻きひげのメッシュを作る"
    bl_options = {"REGISTER", "UNDO"}

    tree_name: StringProperty(options={"SKIP_SAVE"})

    def execute(self, context):
        tree = bpy.data.node_groups.get(self.tree_name) if self.tree_name else None
        body = tree.body if tree is not None and tree.body is not None else _need_body(self, context)
        if body is None:
            return {"CANCELLED"}
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        tree = _tree_for(context, body, self.tree_name)
        obj = groom_tree.run_build(context, tree, self.report)
        return {"FINISHED"} if obj is not None else {"CANCELLED"}


class VINEDRESS_OT_generate(bpy.types.Operator):
    bl_idname = "vine_dress.generate"
    bl_label = "おまかせ一括生成"
    bl_description = "ガイドの自動生成と、つるの生成をまとめて行う"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        body = _need_body(self, context)
        if body is None:
            return {"CANCELLED"}
        try:
            pipeline.make_guides(self.report, context, context.scene.vine_dress, body)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self.report({"ERROR"}, "ガイド生成に失敗しました: %s" % exc)
            return {"CANCELLED"}
        obj = groom_tree.run_build(context, _tree_for(context, body), self.report)
        return {"FINISHED"} if obj is not None else {"CANCELLED"}


class VINEDRESS_OT_clear(bpy.types.Operator):
    bl_idname = "vine_dress.clear"
    bl_label = "つるを削除"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        body = _need_body(self, context)
        if body is None:
            return {"CANCELLED"}
        pipeline.remove_generated(body.name)
        return {"FINISHED"}


# ======================================================================
# Groups
# ======================================================================
class VINEDRESS_OT_group_add(bpy.types.Operator):
    bl_idname = "vine_dress.group_add"
    bl_label = "グループを追加"
    bl_description = "新しいガイドグループを作り、グルームツリーにも追加する"
    bl_options = {"REGISTER", "UNDO"}

    kind: EnumProperty(name="種類", items=guides.KIND_ITEMS)

    def execute(self, context):
        body = _need_body(self, context)
        if body is None:
            return {"CANCELLED"}
        name = "VG_Body" if self.kind == guides.KIND_BODY else "VG_Skirt"
        obj = guides.new_group(context, body, self.kind, name + "_Group")
        groom_tree.tree_add_group(groom_tree.ensure_tree(context, body), obj)
        _set_active(context, obj)
        guides.redraw()
        return {"FINISHED"}


class VINEDRESS_OT_group_remove(bpy.types.Operator):
    bl_idname = "vine_dress.group_remove"
    bl_label = "グループを削除"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = active_group(context, create=False)
        if obj is None:
            return {"CANCELLED"}
        body = obj.vine_guide.body
        groom_tree.remove_group_nodes(obj)
        guides.remove_group(obj)
        rest = guides.guide_objects(body)
        context.scene.vine_dress.active_group_index = list(bpy.data.objects).index(rest[0]) if rest else -1
        guides.redraw()
        groom_tree.schedule_for_body(body)
        return {"FINISHED"}


def _edits(body, only_visible=True):
    return [GroupEdit(o) for o in guides.guide_objects(body) if o.vine_guide.visible or not only_visible]


class VINEDRESS_OT_select_guides(bpy.types.Operator):
    bl_idname = "vine_dress.select_guides"
    bl_label = "ガイドの選択"
    bl_options = {"REGISTER", "UNDO"}

    action: EnumProperty(items=[("SELECT", "全選択", ""), ("DESELECT", "選択解除", ""),
                                ("INVERT", "反転", ""), ("GROUP", "アクティブグループを選択", "")])

    def execute(self, context):
        body = _need_body(self, context)
        if body is None:
            return {"CANCELLED"}
        act = active_group(context, create=False)
        for ge in _edits(body):
            for sp in ge.splines:
                if self.action == "SELECT":
                    sp.sel = True
                elif self.action == "DESELECT":
                    sp.sel = False
                elif self.action == "INVERT":
                    sp.sel = not sp.sel
                else:
                    sp.sel = sp.sel or ge.obj == act
            ge.save()
        guides.redraw()
        return {"FINISHED"}


class VINEDRESS_OT_delete_selected(bpy.types.Operator):
    bl_idname = "vine_dress.delete_selected"
    bl_label = "選択ガイドを削除"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        body = _need_body(self, context)
        if body is None:
            return {"CANCELLED"}
        n = 0
        for ge in _edits(body):
            if ge.obj.vine_guide.locked:
                continue
            keep = [s for s in ge.splines if not s.sel]
            n += len(ge.splines) - len(keep)
            if len(keep) != len(ge.splines):
                ge.splines = keep
                ge.structure = True
                ge.save()
        self.report({"INFO"}, "%d 本削除しました" % n)
        guides.redraw()
        groom_tree.schedule_for_body(body)
        return {"FINISHED"}


class VINEDRESS_OT_move_selected(bpy.types.Operator):
    bl_idname = "vine_dress.move_selected"
    bl_label = "選択をグループへ移動"
    bl_description = "選択中のガイドをアクティブグループ（または新しいグループ）へ移動する"
    bl_options = {"REGISTER", "UNDO"}

    new_group: bpy.props.BoolProperty(name="新しいグループへ", default=False, options={"SKIP_SAVE"})

    def execute(self, context):
        body = _need_body(self, context)
        if body is None:
            return {"CANCELLED"}
        edits = _edits(body)
        picked = [(ge, s) for ge in edits for s in ge.splines if s.sel]
        if not picked:
            self.report({"WARNING"}, "ガイドが選択されていません")
            return {"CANCELLED"}
        if self.new_group:
            target = guides.new_group(context, body, picked[0][0].kind, "VG_Group")
            groom_tree.tree_add_group(groom_tree.ensure_tree(context, body), target)
            _set_active(context, target)
        else:
            target = active_group(context)
        tge = next((ge for ge in edits if ge.obj == target), None)
        if tge is None:
            tge = GroupEdit(target)
            edits.append(tge)
        moved = 0
        for ge, s in picked:
            if ge is tge:
                continue
            ge.splines.remove(s)
            ge.structure = True
            tge.splines.append(s)
            tge.structure = True
            moved += 1
        for ge in edits:
            if ge.structure:
                ge.save()
        self.report({"INFO"}, "%d 本を %s へ移動しました" % (moved, target.name))
        guides.redraw()
        groom_tree.schedule_for_body(body)
        return {"FINISHED"}


class VINEDRESS_OT_snap_guides(bpy.types.Operator):
    bl_idname = "vine_dress.snap_guides"
    bl_label = "体に吸着"
    bl_description = "体表面グループのガイドを体の表面に戻す（選択があれば選択だけ）"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        body = _need_body(self, context)
        if body is None:
            return {"CANCELLED"}
        P = context.scene.vine_dress
        with binding.rest_pose(context, body, P.rest_pose):
            sampler = BodySampler(context, body)
            snap = Snapper(sampler, pipeline.guide_hover(P, pipeline.body_scale(body, P)))
            edits = [ge for ge in _edits(body)
                     if ge.kind == guides.KIND_BODY and not ge.obj.vine_guide.locked]
            any_sel = any(s.sel for ge in edits for s in ge.splines)
            for ge in edits:
                for s in ge.splines:
                    if s.sel or not any_sel:
                        s.pts = [snap(ge.kind, p) for p in s.pts]
                ge.save()
        guides.redraw()
        groom_tree.schedule_for_body(body)
        return {"FINISHED"}


class VINEDRESS_OT_tree_sync_groups(bpy.types.Operator):
    bl_idname = "vine_dress.tree_sync_groups"
    bl_label = "未接続グループを追加"
    bl_description = "グルームツリーに入っていないガイドグループの入力ノードを追加する"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        body = _need_body(self, context)
        if body is None:
            return {"CANCELLED"}
        tree = groom_tree.ensure_tree(context, body)
        for g in guides.guide_objects(body):
            groom_tree.tree_add_group(tree, g)
        return {"FINISHED"}


class VINEDRESS_OT_open_tree(bpy.types.Operator):
    bl_idname = "vine_dress.open_tree"
    bl_label = "ノードエディタで開く"
    bl_description = "グルームツリーをノードエディタに表示する（無ければ新しいウィンドウを開く）"

    def execute(self, context):
        body = _need_body(self, context)
        if body is None:
            return {"CANCELLED"}
        tree = _tree_for(context, body)
        for win in context.window_manager.windows:
            for area in win.screen.areas:
                if area.type == "NODE_EDITOR":
                    area.ui_type = groom_tree.TREE_ID
                    area.spaces.active.node_tree = tree
                    return {"FINISHED"}
        bpy.ops.wm.window_new()
        win = context.window_manager.windows[-1]
        area = win.screen.areas[0]
        area.ui_type = groom_tree.TREE_ID
        area.spaces.active.node_tree = tree
        return {"FINISHED"}


# ======================================================================
# Misc
# ======================================================================
class VINEDRESS_OT_toggle_rest(bpy.types.Operator):
    bl_idname = "vine_dress.toggle_rest"
    bl_label = "レスト/ポーズ切替"
    bl_description = "人物のアーマチュアをレストポーズ/ポーズで切り替える（ガイドはレストポーズ基準）"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        body = _need_body(self, context)
        if body is None:
            return {"CANCELLED"}
        for arm in binding.body_armatures(body):
            arm.data.pose_position = "POSE" if arm.data.pose_position == "REST" else "REST"
        guides.redraw()
        return {"FINISHED"}


class VINEDRESS_OT_use_active(bpy.types.Operator):
    bl_idname = "vine_dress.use_active"
    bl_label = "選択中を人物に設定"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = context.active_object
        if obj is None or obj.type != "MESH":
            self.report({"ERROR"}, "メッシュを選択してください")
            return {"CANCELLED"}
        context.scene.vine_dress.body = obj
        found = groom_tree.trees_of(obj)
        if found:
            context.scene.vine_dress.groom_tree = found[0]
        return {"FINISHED"}


class VINEDRESS_OT_height_from_cursor(bpy.types.Operator):
    bl_idname = "vine_dress.height_from_cursor"
    bl_label = "3Dカーソルの高さを使う"
    bl_description = "3Dカーソルの高さを身長に対する割合に変換して設定する"
    bl_options = {"REGISTER", "UNDO"}

    prop: StringProperty()

    def execute(self, context):
        P = context.scene.vine_dress
        body = pipeline.get_body(context)
        if body is None:
            return {"CANCELLED"}
        mw = body.matrix_world
        zs = [(mw @ v.co).z for v in body.data.vertices]
        if not zs:
            return {"CANCELLED"}
        zmin, zmax = min(zs), max(zs)
        setattr(P, self.prop, (context.scene.cursor.location.z - zmin) / max(zmax - zmin, 1e-6))
        return {"FINISHED"}


classes = (
    VINEDRESS_OT_generate_guides,
    VINEDRESS_OT_build,
    VINEDRESS_OT_generate,
    VINEDRESS_OT_clear,
    VINEDRESS_OT_group_add,
    VINEDRESS_OT_group_remove,
    VINEDRESS_OT_select_guides,
    VINEDRESS_OT_delete_selected,
    VINEDRESS_OT_move_selected,
    VINEDRESS_OT_snap_guides,
    VINEDRESS_OT_tree_sync_groups,
    VINEDRESS_OT_open_tree,
    VINEDRESS_OT_toggle_rest,
    VINEDRESS_OT_use_active,
    VINEDRESS_OT_height_from_cursor,
)
