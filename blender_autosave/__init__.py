# SPDX-License-Identifier: GPL-3.0-or-later
"""
Auto Save Pro - 一定間隔で .blend を自動保存する Blender アドオン

機能:
  * 指定した分ごとに自動保存（変更があるときだけ保存する設定も可）
  * 保存方法: バックアップ（タイムスタンプ付きコピー）/ 上書き保存 / 両方
  * バックアップの世代数を制限して古いものを自動削除
  * 未保存（Untitled）ファイルもバックアップフォルダへ保存
  * レンダリング中・モーダル操作中（移動/回転など）は保存を延期
  * 3D ビューのサイドバー (N パネル) > AutoSave タブから操作
"""

bl_info = {
    "name": "Auto Save Pro",
    "author": "naturalart9797",
    "version": (1, 0, 0),
    "blender": (2, 93, 0),
    "location": "3D Viewport > Sidebar (N) > AutoSave / Preferences > Add-ons",
    "description": "一定間隔で自動保存し、タイムスタンプ付きバックアップを世代管理します",
    "category": "System",
}

import os
import re
import time
import glob
import datetime

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty

ADDON_ID = __package__ or __name__
DEFAULT_UNTITLED_DIR = os.path.join(os.path.expanduser("~"), "Blender_AutoSave")
RETRY_SECONDS = 5.0

# 実行時の状態（ファイルには保存しない）
_state = {
    "rendering": False,
    "last_save": 0.0,      # time.time()
    "last_msg": "まだ保存していません",
    "next_due": 0.0,
}


# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------

def get_prefs():
    addon = bpy.context.preferences.addons.get(ADDON_ID)
    return addon.preferences if addon else None


def _redraw_ui():
    wm = bpy.context.window_manager
    if not wm:
        return
    for win in wm.windows:
        for area in win.screen.areas:
            if area.type in {"VIEW_3D", "PREFERENCES"}:
                area.tag_redraw()


def _sanitize(name):
    return re.sub(r'[\\/:*?"<>|]+', "_", name).strip() or "untitled"


def backup_dir(prefs):
    """バックアップを置くフォルダの絶対パス。"""
    blend = bpy.data.filepath
    if prefs.backup_dir.strip():
        path = bpy.path.abspath(prefs.backup_dir) if blend else os.path.expanduser(prefs.backup_dir)
        # 未保存ファイルで "//" 相対パスは解決できないのでフォールバック
        if path.startswith("//"):
            path = DEFAULT_UNTITLED_DIR
    elif blend:
        path = os.path.join(os.path.dirname(blend), "autosave")
    else:
        path = DEFAULT_UNTITLED_DIR
    return os.path.normpath(path)


def base_name():
    blend = bpy.data.filepath
    if blend:
        return _sanitize(os.path.splitext(os.path.basename(blend))[0])
    return "untitled"


def _blocking_reason():
    """保存を延期すべき理由を返す（None なら保存して良い）。"""
    if _state["rendering"]:
        return "レンダリング中"
    if getattr(bpy.app, "is_job_running", None):
        try:
            for job in ("RENDER", "RENDER_PREVIEW", "OBJECT_BAKE", "COMPOSITE"):
                if bpy.app.is_job_running(job):
                    return "ジョブ実行中"
        except Exception:
            pass
    wm = bpy.context.window_manager
    if wm:
        for win in wm.windows:
            if len(getattr(win, "modal_operators", ())) > 0:
                return "操作中"
    return None


def _call_with_window(op, **kwargs):
    """タイマーからでもオペレーターが動くよう、ウィンドウのコンテキストを与えて呼ぶ。"""
    wm = bpy.context.window_manager
    win = wm.windows[0] if wm and wm.windows else None
    if win is not None and hasattr(bpy.context, "temp_override"):
        with bpy.context.temp_override(window=win, screen=win.screen):
            return op(**kwargs)
    if win is not None:  # Blender 3.1 以前
        override = bpy.context.copy()
        override["window"] = win
        override["screen"] = win.screen
        return op(override, **kwargs)
    return op(**kwargs)


def rotate_backups(folder, base, keep):
    if keep <= 0:
        return 0
    pattern = os.path.join(glob.escape(folder), glob.escape(base) + "_autosave_*.blend")
    files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
    removed = 0
    for old in files[keep:]:
        try:
            os.remove(old)
            removed += 1
        except OSError as e:
            print(f"[AutoSave] 削除失敗: {old}: {e}")
    return removed


# ---------------------------------------------------------------------------
# 保存処理本体
# ---------------------------------------------------------------------------

def do_autosave(force=False):
    """自動保存を 1 回行う。戻り値: (成功したか, メッセージ)"""
    prefs = get_prefs()
    if prefs is None:
        return False, "設定が見つかりません"

    if not force and prefs.only_if_dirty and not bpy.data.is_dirty:
        return True, "変更なし（スキップ）"

    mode = prefs.save_mode
    done = []
    try:
        # 上書き保存（保存済みファイルのみ）
        if mode in {"OVERWRITE", "BOTH"} and bpy.data.filepath:
            _call_with_window(
                bpy.ops.wm.save_mainfile,
                filepath=bpy.data.filepath,
                compress=prefs.compress,
            )
            done.append("上書き")

        # バックアップ（未保存ファイルは OVERWRITE モードでもこちらへ）
        if mode in {"BACKUP", "BOTH"} or not bpy.data.filepath:
            folder = backup_dir(prefs)
            os.makedirs(folder, exist_ok=True)
            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            base = base_name()
            path = os.path.join(folder, f"{base}_autosave_{stamp}.blend")
            _call_with_window(
                bpy.ops.wm.save_as_mainfile,
                filepath=path,
                copy=True,            # 作業中のファイルパスは変えない
                compress=prefs.compress,
                check_existing=False,
            )
            removed = rotate_backups(folder, base, prefs.max_backups)
            msg = f"バックアップ: {os.path.basename(path)}"
            if removed:
                msg += f"（古い {removed} 件を削除）"
            done.append(msg)
    except Exception as e:  # 失敗しても Blender を止めない
        print(f"[AutoSave] 保存失敗: {e}")
        _state["last_msg"] = f"保存失敗: {e}"
        _redraw_ui()
        return False, _state["last_msg"]

    _state["last_save"] = time.time()
    _state["last_msg"] = " / ".join(done) if done else "保存対象なし"
    print(f"[AutoSave] {time.strftime('%H:%M:%S')} {_state['last_msg']}")
    _redraw_ui()
    return True, _state["last_msg"]


def autosave_timer():
    """bpy.app.timers から呼ばれる。戻り値は次回までの秒数。"""
    prefs = get_prefs()
    if prefs is None:
        return None  # アドオン無効化済み → タイマー停止
    interval = max(1, prefs.interval) * 60.0
    if not prefs.enabled:
        _state["next_due"] = 0.0
        return 10.0  # 有効化されるのを待つ

    reason = _blocking_reason()
    if reason:
        _state["last_msg"] = f"{reason}のため延期"
        _state["next_due"] = time.time() + RETRY_SECONDS
        return RETRY_SECONDS

    do_autosave()
    _state["next_due"] = time.time() + interval
    return interval


def restart_timer(first_delay=None):
    if bpy.app.timers.is_registered(autosave_timer):
        bpy.app.timers.unregister(autosave_timer)
    prefs = get_prefs()
    if first_delay is None:
        first_delay = max(1, prefs.interval) * 60.0 if prefs else 60.0
    _state["next_due"] = time.time() + first_delay
    bpy.app.timers.register(autosave_timer, first_interval=first_delay, persistent=True)


def _on_settings_changed(self, context):
    restart_timer()


# ---------------------------------------------------------------------------
# ハンドラ
# ---------------------------------------------------------------------------

@persistent
def _render_start(*_):
    _state["rendering"] = True


@persistent
def _render_end(*_):
    _state["rendering"] = False


@persistent
def _on_load_post(*_):
    # ファイルを開き直したら間隔をリセット
    restart_timer()


# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------

class AUTOSAVE_Preferences(bpy.types.AddonPreferences):
    bl_idname = ADDON_ID

    enabled: BoolProperty(
        name="自動保存を有効化", default=True, update=_on_settings_changed)
    interval: IntProperty(
        name="間隔（分）", default=5, min=1, max=240, update=_on_settings_changed)
    save_mode: EnumProperty(
        name="保存方法",
        items=[
            ("BACKUP", "バックアップ", "タイムスタンプ付きの別ファイルとして保存（元ファイルはそのまま）"),
            ("OVERWRITE", "上書き保存", "作業中の .blend を上書き保存（未保存ファイルはバックアップへ）"),
            ("BOTH", "両方", "上書き保存とバックアップの両方"),
        ],
        default="BACKUP",
    )
    only_if_dirty: BoolProperty(
        name="変更があるときだけ保存", default=True)
    max_backups: IntProperty(
        name="バックアップ保持数", default=10, min=0, max=999,
        description="ファイルごとに残すバックアップの数（0 = 無制限）")
    backup_dir: StringProperty(
        name="保存先フォルダ", subtype="DIR_PATH", default="",
        description="空欄: .blend と同じ場所の autosave フォルダ（未保存ファイルは ~/Blender_AutoSave）")
    compress: BoolProperty(
        name="圧縮して保存", default=False)

    def draw(self, context):
        draw_settings(self.layout, self)


def draw_settings(layout, prefs):
    col = layout.column()
    col.prop(prefs, "enabled")
    sub = col.column()
    sub.active = prefs.enabled
    sub.prop(prefs, "interval")
    sub.prop(prefs, "save_mode")
    sub.prop(prefs, "only_if_dirty")
    sub.prop(prefs, "compress")
    box = sub.box()
    box.active = prefs.save_mode != "OVERWRITE"
    box.prop(prefs, "backup_dir")
    box.prop(prefs, "max_backups")
    box.label(text="→ " + backup_dir(prefs), icon="FILE_FOLDER")


# ---------------------------------------------------------------------------
# オペレーター
# ---------------------------------------------------------------------------

class AUTOSAVE_OT_save_now(bpy.types.Operator):
    bl_idname = "autosave.save_now"
    bl_label = "今すぐ保存"
    bl_description = "設定に従って今すぐ自動保存を実行します"

    def execute(self, context):
        ok, msg = do_autosave(force=True)
        self.report({"INFO"} if ok else {"ERROR"}, "AutoSave: " + msg)
        restart_timer()
        return {"FINISHED"} if ok else {"CANCELLED"}


class AUTOSAVE_OT_open_folder(bpy.types.Operator):
    bl_idname = "autosave.open_folder"
    bl_label = "保存先を開く"
    bl_description = "バックアップフォルダをファイルブラウザで開きます"

    def execute(self, context):
        folder = backup_dir(get_prefs())
        os.makedirs(folder, exist_ok=True)
        bpy.ops.wm.path_open(filepath=folder)
        return {"FINISHED"}


class AUTOSAVE_OT_load_latest(bpy.types.Operator):
    bl_idname = "autosave.load_latest"
    bl_label = "最新バックアップを開く"
    bl_description = "このファイルの最新の自動保存バックアップを開きます（未保存の変更は失われます）"

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        folder = backup_dir(get_prefs())
        pattern = os.path.join(glob.escape(folder), glob.escape(base_name()) + "_autosave_*.blend")
        files = glob.glob(pattern)
        if not files:
            self.report({"WARNING"}, "バックアップが見つかりません")
            return {"CANCELLED"}
        latest = max(files, key=os.path.getmtime)
        bpy.ops.wm.open_mainfile(filepath=latest)
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# UI パネル
# ---------------------------------------------------------------------------

class AUTOSAVE_PT_panel(bpy.types.Panel):
    bl_label = "Auto Save"
    bl_idname = "AUTOSAVE_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AutoSave"

    def draw_header(self, context):
        prefs = get_prefs()
        if prefs:
            self.layout.prop(prefs, "enabled", text="")

    def draw(self, context):
        prefs = get_prefs()
        layout = self.layout
        if prefs is None:
            layout.label(text="設定が読み込めません")
            return

        status = layout.box()
        if _state["last_save"]:
            ago = int(time.time() - _state["last_save"])
            status.label(text=f"最終保存: {time.strftime('%H:%M:%S', time.localtime(_state['last_save']))}"
                              f"（{ago // 60}分{ago % 60}秒前）", icon="CHECKMARK")
        status.label(text=_state["last_msg"], icon="INFO")
        if prefs.enabled and _state["next_due"]:
            remain = max(0, int(_state["next_due"] - time.time()))
            status.label(text=f"次回: 約 {remain // 60}分{remain % 60}秒後", icon="TIME")

        row = layout.row(align=True)
        row.scale_y = 1.3
        row.operator(AUTOSAVE_OT_save_now.bl_idname, icon="FILE_TICK")
        row = layout.row(align=True)
        row.operator(AUTOSAVE_OT_open_folder.bl_idname, icon="FILE_FOLDER")
        row.operator(AUTOSAVE_OT_load_latest.bl_idname, icon="RECOVER_LAST", text="最新を開く")

        layout.separator()
        draw_settings(layout, prefs)


# ---------------------------------------------------------------------------
# 登録
# ---------------------------------------------------------------------------

classes = (
    AUTOSAVE_Preferences,
    AUTOSAVE_OT_save_now,
    AUTOSAVE_OT_open_folder,
    AUTOSAVE_OT_load_latest,
    AUTOSAVE_PT_panel,
)

_handlers = (
    ("render_init", _render_start),
    ("render_complete", _render_end),
    ("render_cancel", _render_end),
    ("load_post", _on_load_post),
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    for name, fn in _handlers:
        lst = getattr(bpy.app.handlers, name)
        if fn not in lst:
            lst.append(fn)
    restart_timer()


def unregister():
    if bpy.app.timers.is_registered(autosave_timer):
        bpy.app.timers.unregister(autosave_timer)
    for name, fn in _handlers:
        lst = getattr(bpy.app.handlers, name)
        if fn in lst:
            lst.remove(fn)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
