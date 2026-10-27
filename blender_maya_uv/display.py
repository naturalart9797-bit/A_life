# Display helpers: checker (Maya: Checkered tiles), thick shell borders
# (Maya: Toggle texture borders) and switching to the UV workspace.

import bpy

from .common import settings, edit_meshes, is_border_loop


CHECKER_NAME = "MayaUV_Checker"


# ---------------------------------------------------------------------------
# Checker
# ---------------------------------------------------------------------------

def checker_image(s):
    img = bpy.data.images.get(CHECKER_NAME)
    if img is None:
        img = bpy.data.images.new(CHECKER_NAME, s.checker_tiles, s.checker_tiles)
    if img.generated_type != s.checker_type:
        img.generated_type = s.checker_type
    if img.generated_width != s.checker_tiles:
        img.generated_width = img.generated_height = s.checker_tiles
    return img


def update_checker_image(s):
    if bpy.data.images.get(CHECKER_NAME) is not None:
        checker_image(s)


def _checker_material(s):
    mat = bpy.data.materials.get(CHECKER_NAME)
    if mat is None:
        mat = bpy.data.materials.new(CHECKER_NAME)
        if hasattr(mat, "use_nodes") and not mat.use_nodes:
            mat.use_nodes = True
        nt = mat.node_tree
        bsdf = next((n for n in nt.nodes if n.type == 'BSDF_PRINCIPLED'), None)
        if bsdf is None:
            bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
            out = next((n for n in nt.nodes if n.type == 'OUTPUT_MATERIAL'), None) or \
                nt.nodes.new("ShaderNodeOutputMaterial")
            nt.links.new(bsdf.outputs[0], out.inputs[0])
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.location = (bsdf.location.x - 300, bsdf.location.y)
        nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
        nt.nodes.active = tex
    tex = next(n for n in mat.node_tree.nodes if n.type == 'TEX_IMAGE')
    tex.image = checker_image(s)
    return mat


def _view3d_spaces(context):
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                yield area.spaces.active


class MAYA_UV_OT_checker(bpy.types.Operator):
    """Show a checker texture on all objects (Maya: Checkered tiles). Press again to restore"""
    bl_idname = "maya_uv.checker"
    bl_label = "Checker"
    bl_options = {'REGISTER'}

    def execute(self, context):
        s = settings(context)
        view_layer = context.view_layer
        if not s.checker_on:
            view_layer.material_override = _checker_material(s)
            spaces = list(_view3d_spaces(context))
            s.checker_prev_shading = ",".join(sp.shading.type for sp in spaces)
            for sp in spaces:
                if sp.shading.type in {'SOLID', 'WIREFRAME'}:
                    sp.shading.type = 'MATERIAL'
            for area in context.screen.areas:
                if area.type == 'IMAGE_EDITOR' and area.spaces.active.image is None:
                    area.spaces.active.image = checker_image(s)
            s.checker_on = True
        else:
            if view_layer.material_override and view_layer.material_override.name == CHECKER_NAME:
                view_layer.material_override = None
            prev = s.checker_prev_shading.split(",") if s.checker_prev_shading else []
            for sp, shading in zip(_view3d_spaces(context), prev):
                if shading:
                    sp.shading.type = shading
            for area in context.screen.areas:
                if area.type == 'IMAGE_EDITOR':
                    img = area.spaces.active.image
                    if img is not None and img.name == CHECKER_NAME:
                        area.spaces.active.image = None
            s.checker_on = False
        return {'FINISHED'}


class MAYA_UV_OT_open_editor(bpy.types.Operator):
    """Switch to the UV Editing workspace"""
    bl_idname = "maya_uv.open_editor"
    bl_label = "UV Editor"

    def execute(self, context):
        ws = bpy.data.workspaces.get("UV Editing")
        if ws is None:
            self.report({'WARNING'}, "No 'UV Editing' workspace in this file")
            return {'CANCELLED'}
        context.window.workspace = ws
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Texture borders overlay
# ---------------------------------------------------------------------------

BORDER_COLOR = (0.35, 0.8, 1.0, 1.0)
_draw_handle = None
_draw_failed = False


def _draw_borders():
    global _draw_failed
    context = bpy.context
    if _draw_failed or context.mode != 'EDIT_MESH':
        return
    s = settings(context)
    if not s.show_texture_borders:
        return
    try:
        import gpu
        from gpu_extras.batch import batch_for_shader

        region = context.region
        v2d = region.view2d
        coords = []
        for ob, bm, uv, faces in edit_meshes(context):
            face_set = set(faces)
            for f in faces:
                for l in f.loops:
                    if is_border_loop(l, uv, face_set):
                        for co in (l[uv].uv, l.link_loop_next[uv].uv):
                            x, y = v2d.view_to_region(co.x, co.y, clip=False)
                            coords.append((x, y, 0.0))
        if not coords:
            return
        shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
        shader.uniform_float("viewportSize", (region.width, region.height))
        shader.uniform_float("lineWidth", s.border_width)
        shader.uniform_float("color", BORDER_COLOR)
        batch = batch_for_shader(shader, 'LINES', {"pos": coords})
        gpu.state.blend_set('ALPHA')
        batch.draw(shader)
        gpu.state.blend_set('NONE')
    except Exception as exc:  # never break the editor's drawing
        _draw_failed = True
        print("Maya UV Toolkit: texture borders disabled:", exc)


def register_draw():
    global _draw_handle
    _draw_handle = bpy.types.SpaceImageEditor.draw_handler_add(_draw_borders, (), 'WINDOW', 'POST_PIXEL')


def unregister_draw():
    global _draw_handle
    if _draw_handle is not None:
        bpy.types.SpaceImageEditor.draw_handler_remove(_draw_handle, 'WINDOW')
        _draw_handle = None


classes = (
    MAYA_UV_OT_checker,
    MAYA_UV_OT_open_editor,
)
