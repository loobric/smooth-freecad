# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
The three tabs of the Loobric window (LoobricDialog.py), plus shared widgets.

The information architecture is deliberately small (reboot Phase 3):

- **Sync** — the one CAM surface: a hierarchical ToolSet→tool plan of the local
  FreeCAD tool directory against the server, with an "out of sync" filter
  (it is a *view* over the single plan, not a second screen) and the checkbox
  apply model (direction is derived from status; the user only chooses
  inclusion). Tool/ToolSet management (rename, set type, delete, setup status)
  lives here as row actions; a conflict/deletion opens a side-by-side
  resolution that applies immediately.
- **Machines** — the one binding surface: each machine's tool table, with pending
  binding proposals from the Inbox folded in (Confirm/Reject inline) and manual
  bind / bind-new / unbind.
- **Audit** — the read-only operation log.

All sync/plan logic lives in the headless-tested modules (sync.py, mapping.py,
client.py) and the pure **view-model** (viewmodel.py, which answers "what should
the window show"); these classes only render that and fire actions. Two shared
debug widgets (RecordInspector, ApiLogPanel) are demoted behind the window's
"Debug" menu.
"""
import json

import FreeCAD as App
from PySide import QtGui, QtCore

from . import sync, mapping, viewmodel
from .client import LoobricError
from .viewmodel import (field_value, canonical as _canonical, short_id,
                        record_name, instance_shape, instance_diameter,
                        fmt_dia as _fmt_dia, resolution_rows,
                        resolution_actions, RESOLUTION_DECISION)


# A light tint + dark foreground for rows that have moved away from sync, legible
# on both light and dark FreeCAD themes.
_FG_ON_TINT = QtGui.QColor(33, 33, 33)
TINT_WARNING = QtGui.QColor(255, 235, 190)
TINT_CRITICAL = QtGui.QColor(255, 205, 205)


# ---------------------------------------------------------------------------
# Shared debug widgets (reached from the window's Debug menu)
# ---------------------------------------------------------------------------

class RecordInspector(QtGui.QDialog):
    """Pretty-print a raw sectioned record (the web UI's showRecord modal)."""

    def __init__(self, record, parent=None, title="Record"):
        super().__init__(parent)
        self.setWindowTitle("Inspect: %s" % title)
        self.resize(560, 600)
        layout = QtGui.QVBoxLayout(self)
        view = QtGui.QPlainTextEdit()
        view.setReadOnly(True)
        view.setLineWrapMode(QtGui.QPlainTextEdit.NoWrap)
        font = QtGui.QFont("monospace")
        font.setStyleHint(QtGui.QFont.TypeWriter)
        view.setFont(font)
        try:
            view.setPlainText(json.dumps(record, indent=2, ensure_ascii=False))
        except (TypeError, ValueError):
            view.setPlainText(repr(record))
        layout.addWidget(view)
        close = QtGui.QPushButton("Close")
        close.clicked.connect(self.accept)
        row = QtGui.QHBoxLayout()
        row.addStretch()
        row.addWidget(close)
        layout.addLayout(row)


class ApiLogPanel(QtGui.QDialog):
    """Live view of the client's HTTP traffic for debugging."""

    def __init__(self, client, parent=None):
        super().__init__(parent)
        self.client = client
        self.setWindowTitle("Loobric API log")
        self.resize(620, 360)
        layout = QtGui.QVBoxLayout(self)
        self.view = QtGui.QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setLineWrapMode(QtGui.QPlainTextEdit.NoWrap)
        font = QtGui.QFont("monospace")
        font.setStyleHint(QtGui.QFont.TypeWriter)
        self.view.setFont(font)
        layout.addWidget(self.view)
        row = QtGui.QHBoxLayout()
        clear = QtGui.QPushButton("Clear")
        clear.clicked.connect(self._clear)
        row.addWidget(clear)
        row.addStretch()
        close = QtGui.QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        layout.addLayout(row)
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(1000)
        self.refresh()

    def _clear(self):
        del self.client.call_log[:]
        self.refresh()

    def refresh(self):
        lines = []
        for entry in reversed(self.client.call_log):
            status = entry.get("status")
            flag = "" if status == 200 else "  ⚠ %s" % (entry.get("error") or status)
            lines.append("%-6s %-44s %4s %5dms%s" % (
                entry.get("method", "?"), entry.get("path", "?"),
                status if status is not None else "—",
                entry.get("ms", 0), flag))
        self.view.setPlainText("\n".join(lines))


# ---------------------------------------------------------------------------
# Base tab
# ---------------------------------------------------------------------------

class _Tab(QtGui.QWidget):
    """Common plumbing: the client, error-wrapped actions, the API-log poke,
    and the 'what record is selected' hook the window's Inspect uses."""

    TITLE = "?"

    def __init__(self, window, client):
        super().__init__()
        self.window = window
        self.client = client
        self._loaded = False

    def ensure_loaded(self):
        if not self._loaded:
            self._loaded = True
            self.refresh()

    def refresh(self):
        pass

    def selected_record(self):
        return None

    def _notify(self, message):
        self.window.status(message)

    def act(self, fn, *, confirm=None, success=None, on_conflict=None):
        """Run a server call with uniform error handling. ``confirm`` is a
        (title, text) prompt shown first; ``on_conflict`` handles a 409."""
        if confirm and QtGui.QMessageBox.question(
                self, confirm[0], confirm[1],
                QtGui.QMessageBox.Yes | QtGui.QMessageBox.No) != QtGui.QMessageBox.Yes:
            return None
        try:
            result = fn()
        except LoobricError as e:
            self.window.refresh_api_log()
            if getattr(e, "status", None) == 409 and on_conflict is not None:
                on_conflict(e)
                return None
            QtGui.QMessageBox.warning(self, "Loobric", str(e))
            self._notify("✗ %s" % e)
            return None
        self.window.refresh_api_log()
        if success:
            self._notify(success)
        self.refresh()
        return result


# ---------------------------------------------------------------------------
# Per-object resolution view (side-by-side field comparison)
# ---------------------------------------------------------------------------

class ResolutionDialog(QtGui.QDialog):
    """One object, one resolution: the per-field side-by-side comparison (server
    side annotated with canonical provenance) and Keep Local / Keep Server /
    Skip. All comparison content comes from the pure ``resolution_rows`` helper;
    this class only renders it."""

    def __init__(self, row, parent=None):
        super().__init__(parent)
        self.choice = None
        name = row.get("name") or "object"
        self.setWindowTitle("Resolve: %s" % name)
        self.resize(620, 380)
        layout = QtGui.QVBoxLayout(self)

        header = QtGui.QLabel(
            "<b>%s</b><br>%s" % (name, row.get("detail") or row.get("hint") or ""))
        header.setWordWrap(True)
        layout.addWidget(header)

        rows = resolution_rows(row)
        table = QtGui.QTreeWidget()
        table.setHeaderLabels(["Field", "Local", "Server", "Server source"])
        table.setRootIsDecorated(False)
        table.setColumnWidth(0, 160)
        table.setColumnWidth(1, 150)
        table.setColumnWidth(2, 150)
        for r in rows:
            item = QtGui.QTreeWidgetItem([
                str(r["field"]), repr(r["local"]), repr(r["server"]),
                r["server_source"] or "—"])
            changed = r.get("changed_by")
            if changed in ("local", "both"):
                item.setBackground(1, TINT_WARNING)
                item.setForeground(1, _FG_ON_TINT)
            if changed in ("server", "both"):
                item.setBackground(2, TINT_WARNING)
                item.setForeground(2, _FG_ON_TINT)
            table.addTopLevelItem(item)
        if not rows:
            layout.addWidget(QtGui.QLabel(
                "No field-level differences are recorded for this item (it may be "
                "a whole-object create/delete). Choose which side to keep."))
        layout.addWidget(table, stretch=1)

        buttons = QtGui.QHBoxLayout()
        buttons.addStretch()
        for key, label in resolution_actions(row):
            btn = QtGui.QPushButton(label)
            btn.clicked.connect(lambda _=False, k=key: self._choose(k))
            buttons.addWidget(btn)
        layout.addLayout(buttons)

    def _choose(self, key):
        self.choice = key
        self.accept()

    def get_choice(self):
        return self.choice if self.exec_() == QtGui.QDialog.Accepted else None


# ---------------------------------------------------------------------------
# Sync tab — the one CAM surface (plan/apply + management actions)
# ---------------------------------------------------------------------------

class SyncTab(_Tab):
    """Hierarchical browse of every ToolSet and its tools, each set labelled with
    a derived rollup (✓ synced · ↑ local-only · ↓ server-only · ⚠ conflicts).

    The checkbox apply model (viewmodel.row_apply_info): a row's direction is
    DERIVED from its status — the user only chooses inclusion. Safe rows come
    checked with a fixed ↑/↓ action; a set node's tri-state checkbox toggles
    its rows at once; the Apply button states its plan ('3 uploads, 2
    downloads'). Conflicts and deletions carry no checkbox — double-click
    resolves them field-by-field and applies immediately. Forcing the opposite
    direction is a deliberate act in the row's right-click menu, which also
    holds management (rename, set type, delete, setup status). Bulk Apply
    touches nothing until the button is pressed."""

    TITLE = "Sync"

    STATUS_ICON = {
        "unchanged": "✓", "push": "↑", "pull": "↓", "new_local": "↑",
        "new_server": "↓", "conflict": "⚠", "deleted_local": "✖",
        "deleted_server": "✖", "job_set": "📋",
    }

    def __init__(self, window, client, tools_dir):
        super().__init__(window, client)
        self.tools_dir = tools_dir
        self.plan = {"items": [], "errors": []}
        self._row_items = {}        # item key -> its QTreeWidgetItem
        self._forced = {}           # item key -> forced direction override
        self._shape_widgets = {}
        self._guessed = 0
        self._attention_only = False
        self._build()

    def _build(self):
        layout = QtGui.QVBoxLayout(self)

        top = QtGui.QHBoxLayout()
        hint = QtGui.QLabel(
            "Check the items to sync — each row's ↑/↓ action follows its "
            "status; a 📁 set's checkbox toggles all its tools. Double-click a "
            "⚠/✖ row to resolve it; right-click for management and overrides.")
        hint.setWordWrap(True)
        top.addWidget(hint, stretch=1)
        # A checkable button (not a checkbox) so the on/off state is visible on
        # every FreeCAD theme — some render the checkbox indicator invisibly.
        self.attention_button = QtGui.QPushButton("Out of sync only")
        self.attention_button.setCheckable(True)
        self.attention_button.setToolTip(
            "Show only items that aren't in sync (hide everything already synced).")
        self.attention_button.toggled.connect(self._toggle_attention)
        top.addWidget(self.attention_button)
        layout.addLayout(top)

        self.tree = QtGui.QTreeWidget()
        self.tree.setHeaderLabels(["Item", "Status", "Action", "Tool type"])
        self.tree.setColumnWidth(0, 260)
        self.tree.setColumnWidth(1, 160)
        self.tree.setColumnWidth(2, 180)
        self.tree.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.itemSelectionChanged.connect(self._show_diff)
        self.tree.itemDoubleClicked.connect(lambda *_: self._resolve_selected())
        self.tree.itemChanged.connect(self._item_changed)
        layout.addWidget(self.tree, stretch=3)

        self.diff_pane = QtGui.QTextEdit()
        self.diff_pane.setReadOnly(True)
        self.diff_pane.setMaximumHeight(120)
        self.diff_pane.setPlaceholderText(
            "Select a changed item to see exactly which fields differ, each "
            "attributed to the side that made the change.")
        layout.addWidget(self.diff_pane, stretch=1)

        # No inline log pane — apply progress goes to the status line and the
        # FreeCAD report view; raw HTTP traffic is under Debug ▸ API log.
        buttons = QtGui.QHBoxLayout()
        self.check_all_button = QtGui.QPushButton("Check all")
        self.check_all_button.setToolTip(
            "Check every item with a safe direction. Conflicts and deletions "
            "have no checkbox — resolve those by double-clicking them.")
        self.check_all_button.clicked.connect(lambda: self._check_all(True))
        buttons.addWidget(self.check_all_button)
        self.uncheck_all_button = QtGui.QPushButton("Uncheck all")
        self.uncheck_all_button.setToolTip(
            "Uncheck every item — Apply would then do nothing.")
        self.uncheck_all_button.clicked.connect(lambda: self._check_all(False))
        buttons.addWidget(self.uncheck_all_button)
        buttons.addStretch()
        self.apply_button = QtGui.QPushButton("Apply")
        self.apply_button.setToolTip(
            "Sync every checked item in its shown direction. Nothing touches "
            "disk or the server until this is pressed.")
        self.apply_button.clicked.connect(self._run_apply)
        self.apply_button.setEnabled(False)
        buttons.addWidget(self.apply_button)
        layout.addLayout(buttons)

    def _append(self, message):
        App.Console.PrintMessage("Loobric: %s\n" % message)
        QtGui.QApplication.processEvents()

    def _toggle_attention(self, on):
        self._attention_only = bool(on)
        self._render()

    # -- plan rendering ---------------------------------------------------

    def refresh(self):
        try:
            self.plan = sync.plan_sync(self.tools_dir, self.client,
                                       log=self._append)
        except LoobricError as e:
            self.window.refresh_api_log()
            self._notify("✗ planning failed: %s" % e)
            self._append("Planning failed: %s" % e)
            self.tree.clear()
            self._row_items = {}
            self._forced = {}
            self._update_apply()
            return
        self.window.refresh_api_log()
        for error in self.plan["errors"]:
            self._append("⚠ %s" % error)
        self._render()

    def _render(self):
        """Build the tree from the view-model — the single source of 'what to
        show'. Called on refresh and when the attention filter toggles (no
        re-fetch). Signals are blocked during the rebuild so the checkbox
        handler only ever sees user edits."""
        self.tree.blockSignals(True)
        self.tree.clear()
        self._row_items = {}
        self._forced = {}
        self._shape_widgets = {}
        self._guessed = 0
        model = viewmodel.sync_tree(self.plan["items"],
                                    attention_only=self._attention_only)
        for group in model["groups"]:
            lib_item = group["library_item"]
            is_job_set = lib_item is not None and lib_item.get("action") == "job_set"
            icon = "📋" if is_job_set else (
                "📁" if group["kind"] == "library" else "📄")
            node = QtGui.QTreeWidgetItem(
                ["%s %s" % (icon, group["name"]), group["rollup"], "", ""])
            node.setToolTip(1, group["rollup"] if is_job_set else
                            "Derived from this set's tools — the set itself is "
                            "never 'conflicted'.")
            self.tree.addTopLevelItem(node)
            if lib_item is not None:
                self._add_row(node, lib_item, indent_self=True)
            for bit in group["members"]:
                self._add_row(node, bit)
            self._init_group_checkbox(node)
            node.setExpanded(True)
        self.tree.blockSignals(False)

        pending = model["pending"]
        if pending:
            extra = (" Tool types for %d download(s) were pre-filled from their "
                     "names — review the 'Tool type' column." % self._guessed
                     ) if self._guessed else ""
            self._notify("Plan ready: %d item(s) need attention.%s" % (pending, extra))
        else:
            self._notify("Everything is in sync.")
        self._update_apply()

    def _tool_type_text(self, item):
        """The tool type to show in the 'Tool type' column for a bit row (sets
        carry no type). From the server record's canonical shape, else a guess
        from the name; '—' if nothing is known. A row that needs a download-time
        type CHOICE replaces this text with the editable combo below."""
        if item.get("kind") != "bit":
            return ""
        rec = item.get("record")
        shape = (instance_shape(rec) if rec else None) \
            or mapping.guess_shape_from_name(item.get("name") or "")
        return shape or "—"

    def _add_row(self, parent, item, indent_self=False):
        label = "(this tool set)" if indent_self else item["name"]
        info = viewmodel.row_status_info(item["action"])
        status = "%s %s" % (self.STATUS_ICON.get(item["action"], ""), info["label"])
        apply_info = viewmodel.row_apply_info(item["action"])
        row = QtGui.QTreeWidgetItem([label, status, apply_info["label"],
                                     self._tool_type_text(item)])
        row.setToolTip(1, item["detail"] or info["hint"])
        row.setData(0, QtCore.Qt.UserRole, item["key"])
        parent.addChild(row)
        if apply_info["checkable"]:
            row.setFlags(row.flags() | QtCore.Qt.ItemIsUserCheckable)
            row.setCheckState(0, QtCore.Qt.Checked if apply_info["checked"]
                              else QtCore.Qt.Unchecked)
            self._row_items[item["key"]] = row
        elif apply_info["label"]:
            # A conflict/deletion: no checkbox — resolution is its only path.
            row.setToolTip(2, "No safe default — double-click to choose a side "
                              "(applies immediately).")
        # The download-time tool-type choice stays for bulk-applied downloads;
        # conflicts resolve through the dialog, which doesn't take a type.
        if sync.needs_shape_choice(item) and item["action"] != "conflict":
            shape_combo = QtGui.QComboBox()
            shape_combo.addItems(mapping.FREECAD_SHAPES)
            guess = mapping.guess_shape_from_name(item["name"])
            default = (guess or sync.record_shape(item["record"])
                       or mapping.DEFAULT_SHAPE)
            if default in mapping.FREECAD_SHAPES:
                shape_combo.setCurrentIndex(mapping.FREECAD_SHAPES.index(default))
            if guess:
                self._guessed += 1
            shape_combo.setToolTip(
                "Tool type to create on download (pre-filled from the name). "
                "FreeCAD can't change a bit's type after creation, so correct it "
                "here if the guess is wrong.")
            self.tree.setItemWidget(row, 3, shape_combo)
            self._shape_widgets[item["key"]] = shape_combo

    # -- the checkbox model ------------------------------------------------

    def _init_group_checkbox(self, node):
        """A set node with at least one checkable row gets a tri-state checkbox
        that toggles them all at once (checked/unchecked/partial mirrors its
        rows)."""
        keys = [node.child(i).data(0, QtCore.Qt.UserRole)
                for i in range(node.childCount())]
        if not any(k in self._row_items for k in keys):
            return
        node.setFlags(node.flags() | QtCore.Qt.ItemIsUserCheckable)
        self._sync_group_state(node)

    def _sync_group_state(self, node):
        """Recompute a set node's tri-state from its checkable rows."""
        states = [node.child(i).checkState(0)
                  for i in range(node.childCount())
                  if node.child(i).data(0, QtCore.Qt.UserRole) in self._row_items]
        if not states:
            return
        if all(s == QtCore.Qt.Checked for s in states):
            node.setCheckState(0, QtCore.Qt.Checked)
        elif all(s == QtCore.Qt.Unchecked for s in states):
            node.setCheckState(0, QtCore.Qt.Unchecked)
        else:
            node.setCheckState(0, QtCore.Qt.PartiallyChecked)

    def _item_changed(self, item, column):
        """Checkbox plumbing: a set node's state fans out to its rows; a row's
        change rolls up to its node; either way the Apply plan re-counts."""
        if column != 0:
            return
        self.tree.blockSignals(True)
        try:
            if item.parent() is None:                 # a set/group node
                state = item.checkState(0)
                if state != QtCore.Qt.PartiallyChecked:
                    for i in range(item.childCount()):
                        child = item.child(i)
                        if child.data(0, QtCore.Qt.UserRole) in self._row_items:
                            child.setCheckState(0, state)
            else:
                self._sync_group_state(item.parent())
        finally:
            self.tree.blockSignals(False)
        self._update_apply()

    def _check_all(self, on):
        state = QtCore.Qt.Checked if on else QtCore.Qt.Unchecked
        self.tree.blockSignals(True)
        for row in self._row_items.values():
            row.setCheckState(0, state)
        for i in range(self.tree.topLevelItemCount()):
            self._sync_group_state(self.tree.topLevelItem(i))
        self.tree.blockSignals(False)
        self._update_apply()

    def _update_apply(self):
        """Refresh the Apply button so it always states the current plan."""
        decisions = self._collect_decisions()
        pushes = sum(1 for d in decisions.values() if d == "push")
        pulls = sum(1 for d in decisions.values() if d == "pull")
        self.apply_button.setText(viewmodel.apply_button_text(pushes, pulls))
        self.apply_button.setEnabled(bool(decisions))
        has_rows = bool(self._row_items)
        self.check_all_button.setEnabled(has_rows)
        self.uncheck_all_button.setEnabled(has_rows)

    def _force(self, item, direction):
        """A deliberate against-the-suggestion override from the context menu:
        flip the row's action label and make sure it's checked."""
        key = item["key"]
        self._forced[key] = direction
        row = self._row_items.get(key)
        if row is None:
            return
        self.tree.blockSignals(True)
        row.setText(2, viewmodel.FORCED_LABELS[direction])
        row.setCheckState(0, QtCore.Qt.Checked)
        if row.parent() is not None:
            self._sync_group_state(row.parent())
        self.tree.blockSignals(False)
        self._update_apply()

    def _selected_item(self):
        rows = self.tree.selectedItems()
        if not rows:
            return None
        key = rows[0].data(0, QtCore.Qt.UserRole)
        return next((i for i in self.plan["items"] if i["key"] == key), None)

    def _show_diff(self):
        item = self._selected_item()
        if not item:
            self.diff_pane.setPlainText("")
            return
        lines = [item["detail"]]
        for d in item.get("diff", []):
            side = {"local": "changed here", "server": "changed on server",
                    "both": "changed on BOTH sides"}[d["changed_by"]]
            lines.append("  %s: %r (local)  vs  %r (server)   [%s]"
                         % (d["field"], d["local"], d["server"], side))
        if item["action"] == "new_local":
            lines.append("  (entire file is new to the server)")
        if item["action"] == "new_server":
            lines.append("  (entire record is new - downloading creates the file)")
        self.diff_pane.setPlainText("\n".join(lines))

    def selected_record(self):
        item = self._selected_item()
        return item.get("record") if item else None

    # -- management actions (right-click) ---------------------------------

    def _context_menu(self, point):
        # Select the row under the cursor first, so right-click works without a
        # prior left-click.
        at = self.tree.itemAt(point)
        if at is not None:
            self.tree.setCurrentItem(at)
        item = self._selected_item()
        if not item:
            return
        menu = QtGui.QMenu(self)
        record = item.get("record")
        is_set = item["kind"] == "library"
        inspect = menu.addAction("Inspect record (JSON)…")
        inspect.setEnabled(record is not None)
        # Deliberate against-the-suggestion overrides (modified rows only).
        force_actions = [
            (menu.addAction(label), direction)
            for direction, label in viewmodel.force_options(
                item["action"], item.get("path") is not None,
                record is not None)]
        menu.addSeparator()
        rename = menu.addAction("Rename…")
        rename.setEnabled(record is not None)
        set_type = None
        if not is_set:
            set_type = menu.addAction("Set tool type…")
            set_type.setEnabled(record is not None)
        link = None
        if is_set:
            link = menu.addAction("Setup status…")
            link.setEnabled(record is not None)
        menu.addSeparator()
        delete = menu.addAction("Delete on server…")
        delete.setEnabled(record is not None)
        if record is None:
            menu.addAction("(upload first to manage on the server)").setEnabled(False)

        chosen = menu.exec_(self.tree.viewport().mapToGlobal(point))
        if chosen is None:
            return
        for act, direction in force_actions:
            if chosen == act:
                self._force(item, direction)
                return
        if chosen == inspect:
            self.window.inspect_selected()
        elif chosen == rename:
            self._rename(item)
        elif set_type is not None and chosen == set_type:
            self._set_type(item)
        elif link is not None and chosen == link:
            self._setup_status(item)
        elif chosen == delete:
            self._delete(item)

    def _rename(self, item):
        record = item["record"]
        new, ok = QtGui.QInputDialog.getText(
            self, "Rename", "Name:", text=record_name(record))
        if not ok or not new:
            return
        rid = record["internal"]["id"]
        if item["kind"] == "library":
            self.act(lambda: self.client.assert_set(rid, "name", new), success="Renamed.")
        else:
            self.act(lambda: self.client.assert_instance(rid, "name", new),
                     success="Renamed.")

    def _set_type(self, item):
        record = item["record"]
        current = instance_shape(record) or mapping.DEFAULT_SHAPE
        idx = (mapping.FREECAD_SHAPES.index(current)
               if current in mapping.FREECAD_SHAPES else 0)
        shape, ok = QtGui.QInputDialog.getItem(
            self, "Set tool type", "Type (asserts geometry.shape):",
            mapping.FREECAD_SHAPES, idx, False)
        if not ok:
            return
        rid = record["internal"]["id"]
        self.act(lambda: self.client.assert_instance(rid, "geometry.shape", shape),
                 success="Tool type asserted.")

    def _setup_status(self, item):
        """READ-ONLY: where this set is the active setup, and how machine
        reality compares to its claims. Which machine runs which set is the
        operator's act (`loobric use-set`) — the CAM side sees the truth, it
        doesn't switch it (MAPPING_PLAN: display, not a menu of actions)."""
        record = item["record"]
        sid = record["internal"]["id"]
        try:
            active = [r for r in self.client.active_setups()
                      if r.get("tool_set_id") == sid]
            machines = {m["internal"]["id"]: record_name(m)
                        for m in self.client.list_machines()}
        except LoobricError as e:
            self.window.refresh_api_log()
            QtGui.QMessageBox.warning(self, "Loobric", str(e))
            return
        self.window.refresh_api_log()
        if not active:
            QtGui.QMessageBox.information(
                self, "Setup status",
                "'%s' is not the active setup on any machine — every tool "
                "number in it is provisional (a claim nothing checks).\n\n"
                "An operator activates it with:  loobric use-set <machine> "
                "'%s'" % (record_name(record), record_name(record)))
            return
        lines = []
        for row in active:
            mid = row.get("machine_id")
            mname = machines.get(mid) or (mid or "?")[:8]
            try:
                view = self.client.setup_view(mid)
            except LoobricError:
                lines.append("%s: (view unavailable)" % mname)
                continue
            att = view.get("attention") or {}
            headline = ("READY" if view.get("ready")
                        else "NOT READY (%d need attention)"
                             % att.get("important", 0))
            lines.append("%s — %s" % (mname, headline))
            for claim in view.get("claims") or []:
                if claim.get("state") == "satisfied":
                    continue
                num = (claim.get("number") or {}).get("value")
                obs = (claim.get("observed") or {}).get("value")
                name = claim.get("name") or (claim.get("tool_record_id") or "")[:8]
                state = claim.get("state")
                extra = (" (CAM says T%s, machine has T%s)" % (num, obs)
                         if state == "mismounted" else "")
                lines.append("    T%s  %s — %s%s"
                             % (num if num is not None else "?", name, state, extra))
            notes = att.get("notes", 0)
            if notes:
                lines.append("    %d note(s) — tools mounted that this set "
                             "doesn't claim" % notes)
        QtGui.QMessageBox.information(self, "Setup status", "\n".join(lines))

    def _delete(self, item):
        record = item["record"]
        rid = record["internal"]["id"]
        if item["kind"] == "library":
            self.act(lambda: self.client.delete_set(rid),
                     confirm=("Delete tool set",
                              "Delete the set '%s' on the server? The member tools "
                              "are NOT deleted." % record_name(record)),
                     success="Deleted on server.")
        else:
            self.act(lambda: self.client.delete_instance(rid),
                     confirm=("Delete tool",
                              "Delete '%s' on the server? Any entry holding it is "
                              "unbound first." % record_name(record)),
                     success="Deleted on server.")

    def _resolve_selected(self):
        """Double-click on a changed row → field-by-field resolution, applied
        IMMEDIATELY through the same upload/download write paths as Apply (the
        one deliberate exception to 'nothing happens until Apply')."""
        item = self._selected_item()
        if not item or item["action"] in ("unchanged", "note", "job_set"):
            return
        choice = ResolutionDialog(item, parent=self).get_choice()
        if not choice:
            return
        decision = RESOLUTION_DECISION.get(choice, "skip")
        if decision == "skip":
            self._notify("Skipped %s." % item.get("name"))
            return
        try:
            summary = sync.apply_sync(self.tools_dir, self.client, self.plan,
                                      {item["key"]: decision})
        except LoobricError as e:
            self.window.refresh_api_log()
            QtGui.QMessageBox.warning(self, "Loobric", str(e))
            self._notify("✗ %s" % e)
            return
        self.window.refresh_api_log()
        if summary["errors"]:
            QtGui.QMessageBox.warning(self, "Loobric", "\n".join(summary["errors"]))
        self._notify("Resolved '%s' — %d uploaded, %d downloaded."
                     % (item.get("name"), summary["pushed"], summary["pulled"]))
        self.refresh()

    # -- apply ------------------------------------------------------------

    def _collect_decisions(self):
        """{key: 'push'|'pull'} for every checked row — the forced override
        when one was set, else the status-derived direction."""
        items_by_key = {i["key"]: i for i in self.plan["items"]}
        decisions = {}
        for key, row in self._row_items.items():
            if row.checkState(0) != QtCore.Qt.Checked:
                continue
            item = items_by_key.get(key)
            if item is None:
                continue
            direction = self._forced.get(key) \
                or viewmodel.row_apply_info(item["action"])["direction"]
            if direction:
                decisions[key] = direction
        return decisions

    def _collect_shapes(self):
        return {key: combo.currentText()
                for key, combo in self._shape_widgets.items()}

    def _run_apply(self):
        decisions = self._collect_decisions()
        if not decisions:
            self._notify("Nothing checked to apply.")
            return
        self.apply_button.setEnabled(False)
        try:
            summary = sync.apply_sync(self.tools_dir, self.client, self.plan,
                                      decisions, shapes=self._collect_shapes(),
                                      log=self._append)
        except LoobricError as e:
            self.window.refresh_api_log()
            self._append("Apply failed: %s" % e)
            self._notify("✗ apply failed: %s" % e)
            self.apply_button.setEnabled(True)
            return
        self.window.refresh_api_log()
        self._notify("Done: %d uploaded, %d downloaded, %d skipped, %d error(s)."
                     % (summary["pushed"], summary["pulled"], summary["skipped"],
                        len(summary["errors"])))
        for error in summary["errors"]:
            self._append("  ⚠ %s" % error)
        if summary["pulled"]:
            self._append("Reload the CAM tool library to see downloaded changes "
                         "in the editor.")
        self.refresh()


# ---------------------------------------------------------------------------
# Machines tab — the one binding surface (tool tables + folded-in Inbox)
# ---------------------------------------------------------------------------

class MachinesTab(_Tab):
    """Every machine and its tool-table entries, with pending binding proposals
    from the Inbox folded in: an unbound entry that has a proposal shows it, and
    Confirm/Reject act on it. Manual bind / bind-new / unbind and entry/machine
    deletion live here too — this is the single place binding happens."""

    TITLE = "Machines"

    def __init__(self, window, client):
        super().__init__(window, client)
        layout = QtGui.QVBoxLayout(self)

        self.summary = QtGui.QLabel("")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        self.tree = QtGui.QTreeWidget()
        self.tree.setHeaderLabels(["Machine / entry", "Detail", "Diameter",
                                   "Binding"])
        self.tree.setColumnWidth(0, 240)
        self.tree.setColumnWidth(1, 200)
        self.tree.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.itemSelectionChanged.connect(self._selection_changed)
        self.tree.itemDoubleClicked.connect(lambda *a: self.window.inspect_selected())
        layout.addWidget(self.tree)

        row = QtGui.QHBoxLayout()
        self.confirm = QtGui.QPushButton("Confirm proposal")
        self.confirm.setToolTip("Bind the proposed tool into this entry.")
        self.confirm.clicked.connect(self._confirm)
        row.addWidget(self.confirm)
        self.reject = QtGui.QPushButton("Reject")
        self.reject.setToolTip("Reject this proposal — it won't be offered again.")
        self.reject.clicked.connect(self._reject)
        row.addWidget(self.reject)
        row.addSpacing(16)
        self.bind = QtGui.QPushButton("Bind existing…")
        self.bind.clicked.connect(self._bind)
        row.addWidget(self.bind)
        self.bind_new = QtGui.QPushButton("Bind new…")
        self.bind_new.setToolTip("Create a new tool from this entry and bind it.")
        self.bind_new.clicked.connect(self._bind_new)
        row.addWidget(self.bind_new)
        self.unbind = QtGui.QPushButton("Unbind")
        self.unbind.clicked.connect(self._unbind)
        row.addWidget(self.unbind)
        self.del_entry = QtGui.QPushButton("Delete entry")
        self.del_entry.clicked.connect(self._delete_entry)
        row.addWidget(self.del_entry)
        self.del_machine = QtGui.QPushButton("Delete machine")
        self.del_machine.clicked.connect(self._delete_machine)
        row.addWidget(self.del_machine)
        row.addStretch()
        layout.addLayout(row)
        self._selection_changed()

    def _context_menu(self, point):
        at = self.tree.itemAt(point)
        if at is not None:
            self.tree.setCurrentItem(at)
        if self.selected_record() is None:
            return
        menu = QtGui.QMenu(self)
        inspect = menu.addAction("Inspect record (JSON)…")
        if menu.exec_(self.tree.viewport().mapToGlobal(point)) == inspect:
            self.window.inspect_selected()

    def refresh(self):
        self.tree.clear()
        try:
            machines = self.client.list_machines()
            entries = self.client.list_entries()
            instances = self.client.list_instances()
            proposals = self.client.list_inbox()
        except LoobricError as e:
            self.window.refresh_api_log()
            self.summary.setText("✗ %s" % e)
            return
        self.window.refresh_api_log()

        model = viewmodel.machine_tables(machines, entries, instances, proposals)
        if not model["machines"]:
            QtGui.QTreeWidgetItem(self.tree, ["(no machines on the server yet)"])
            self.summary.setText("No machines on the server yet.")
            self._selection_changed()
            return

        for mach in model["machines"]:
            node = QtGui.QTreeWidgetItem(
                self.tree, ["🛠 %s" % mach["name"], mach["controller"] or "—", "", ""])
            node.setData(0, QtCore.Qt.UserRole, ("machine", mach["machine"], None))
            for e in mach["entries"]:
                tnum = e["tool_number"]
                proposal = e["proposal"]
                if e["bound_instance_id"]:
                    binding = "✓ %s" % (e["bound_name"] or short_id(
                        {"internal": {"id": e["bound_instance_id"]}}))
                elif proposal:
                    conf = proposal.get("confidence")
                    binding = "proposed: %s%s" % (
                        proposal.get("name") or "?",
                        " (%d%%)" % round(conf * 100) if isinstance(conf, (int, float)) else "")
                else:
                    binding = "unbound"
                child = QtGui.QTreeWidgetItem(
                    node, ["T%s" % tnum if tnum is not None else "entry",
                           e["description"] or "—", _fmt_dia(e["diameter"]), binding])
                child.setData(0, QtCore.Qt.UserRole, ("entry", e["entry"], proposal))
                if proposal:
                    for col in range(4):
                        child.setBackground(col, TINT_WARNING)
                        child.setForeground(col, _FG_ON_TINT)
            node.setExpanded(True)

        if model["pending"]:
            self.summary.setText("%d entry(s) pending review — a proposed tool "
                                 "awaits Confirm/Reject." % model["pending"])
        else:
            self.summary.setText("No pending proposals.")
        self._selection_changed()

    def _selected(self):
        rows = self.tree.selectedItems()
        if not rows:
            return None, None, None
        data = rows[0].data(0, QtCore.Qt.UserRole)
        if not data:
            return None, None, None
        return data            # (kind, record, proposal)

    def selected_record(self):
        _kind, record, _proposal = self._selected()
        return record

    def _entry_bound(self, entry):
        return field_value(_canonical(entry, "bound_instance_id"))

    def _confirm(self):
        kind, _entry, proposal = self._selected()
        if kind != "entry" or not proposal:
            return
        self.act(lambda: self.client.confirm_proposal(proposal["proposal_id"]),
                 success="Confirmed — the proposed tool is bound into the entry.")

    def _reject(self):
        kind, _entry, proposal = self._selected()
        if kind != "entry" or not proposal:
            return
        self.act(lambda: self.client.reject_proposal(proposal["proposal_id"]),
                 success="Rejected — that pairing won't be proposed again.")

    def _bind(self):
        kind, entry, _proposal = self._selected()
        if kind != "entry" or self._entry_bound(entry):
            return
        try:
            instances = self.client.list_instances()
        except LoobricError as e:
            self.window.refresh_api_log()
            QtGui.QMessageBox.warning(self, "Loobric", str(e))
            return
        self.window.refresh_api_log()
        if not instances:
            QtGui.QMessageBox.information(self, "Bind", "No tools to bind.")
            return
        labels = ["%s  [%s]" % (record_name(i), short_id(i)) for i in instances]
        choice, ok = QtGui.QInputDialog.getItem(
            self, "Bind existing tool", "Bind which tool into this entry?",
            labels, 0, False)
        if not ok:
            return
        iid = instances[labels.index(choice)]["internal"]["id"]
        eid = entry["internal"]["id"]

        def on_conflict(err):
            if QtGui.QMessageBox.question(
                    self, "Already bound elsewhere", "%s\n\nMove it here?" % err,
                    QtGui.QMessageBox.Yes | QtGui.QMessageBox.No
            ) == QtGui.QMessageBox.Yes:
                self.act(lambda: self.client.bind_entry(eid, iid, move=True),
                         success="Moved the tool into this entry.")

        self.act(lambda: self.client.bind_entry(eid, iid),
                 success="Bound the tool into the entry.", on_conflict=on_conflict)

    def _bind_new(self):
        kind, entry, _proposal = self._selected()
        if kind != "entry" or self._entry_bound(entry):
            return
        eid = entry["internal"]["id"]
        name, ok = QtGui.QInputDialog.getText(
            self, "Bind new tool",
            "Name for the new tool (blank = use the entry's description):")
        if not ok:
            return
        self.act(lambda: self.client.bind_new(eid, name=name or None),
                 success="Created a new tool from the entry and bound it.")

    def _unbind(self):
        kind, entry, _proposal = self._selected()
        if kind != "entry" or not self._entry_bound(entry):
            return
        eid = entry["internal"]["id"]
        self.act(lambda: self.client.unbind_entry(eid),
                 success="Unbound (the tool itself is kept).")

    def _delete_entry(self):
        kind, entry, _proposal = self._selected()
        if kind != "entry":
            return
        eid = entry["internal"]["id"]
        self.act(lambda: self.client.delete_entry(eid),
                 confirm=("Delete entry",
                          "Remove this tool-table entry? If the controller "
                          "re-pushes, it returns."),
                 success="Entry removed.")

    def _delete_machine(self):
        kind, machine, _proposal = self._selected()
        if kind != "machine":
            return
        mid = machine["internal"]["id"]
        result = self.act(
            lambda: self.client.delete_machine(mid),
            confirm=("Delete machine",
                     "Delete '%s' and its tool-table entries? Tools are kept."
                     % record_name(machine)),
            success="Machine deleted.")
        if result and result.get("entries_removed") is not None:
            n = result["entries_removed"]
            self._notify("Machine deleted (%d entr%s removed)."
                         % (n, "y" if n == 1 else "ies"))

    def _selection_changed(self):
        kind, record, proposal = self._selected()
        is_entry = kind == "entry"
        bound = is_entry and bool(self._entry_bound(record))
        self.confirm.setEnabled(is_entry and bool(proposal))
        self.reject.setEnabled(is_entry and bool(proposal))
        self.bind.setEnabled(is_entry and not bound)
        self.bind_new.setEnabled(is_entry and not bound)
        self.unbind.setEnabled(is_entry and bound)
        self.del_entry.setEnabled(is_entry)
        self.del_machine.setEnabled(kind == "machine")


# ---------------------------------------------------------------------------
# Audit tab (read-only)
# ---------------------------------------------------------------------------

class AuditTab(_Tab):
    # "Audit log" is the glossary term (Roles, Tenancy & Security) — the
    # immutable record of who changed what, when. Read-only.
    TITLE = "Audit log"
    HEADERS = ["Time", "Operation", "Entity", "Entity id", "Result"]

    def __init__(self, window, client):
        super().__init__(window, client)
        layout = QtGui.QVBoxLayout(self)
        self.tree = QtGui.QTreeWidget()
        self.tree.setHeaderLabels(self.HEADERS)
        self.tree.setRootIsDecorated(False)
        self.tree.setColumnWidth(0, 200)
        self.tree.setColumnWidth(1, 110)
        self.tree.setColumnWidth(2, 170)
        self.tree.setColumnWidth(3, 100)
        self.tree.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.itemDoubleClicked.connect(lambda *a: self.window.inspect_selected())
        layout.addWidget(self.tree)

    def _context_menu(self, point):
        at = self.tree.itemAt(point)
        if at is not None:
            self.tree.setCurrentItem(at)
        if self.selected_record() is None:
            return
        menu = QtGui.QMenu(self)
        inspect = menu.addAction("Inspect record (JSON)…")
        if menu.exec_(self.tree.viewport().mapToGlobal(point)) == inspect:
            self.window.inspect_selected()

    def refresh(self):
        self.tree.clear()
        try:
            logs = self.client.list_audit(limit=50)
        except LoobricError as e:
            self.window.refresh_api_log()
            self._notify("✗ %s" % e)
            return
        self.window.refresh_api_log()
        if not logs:
            QtGui.QTreeWidgetItem(self.tree, ["(no audit entries)", "", "", "", ""])
            return
        for entry in logs:
            eid = entry.get("entity_id") or ""
            item = QtGui.QTreeWidgetItem([
                entry.get("timestamp") or "—", entry.get("operation") or "—",
                entry.get("entity_type") or "—", eid[:8] if eid else "—",
                entry.get("result") or "—"])
            item.setData(0, QtCore.Qt.UserRole, entry)
            self.tree.addTopLevelItem(item)

    def selected_record(self):
        rows = self.tree.selectedItems()
        return rows[0].data(0, QtCore.Qt.UserRole) if rows else None


# ---------------------------------------------------------------------------
# Catalog tab (read-only browse + create-tool-from-catalog)
# ---------------------------------------------------------------------------

class CatalogTab(_Tab):
    """Browse the catalog records (ToolCatalogRecords) and create a tool from a
    selected one. Read-only browse: no catalog authoring/editing here. 'Create
    tool from catalog' makes an UNBOUND server instance from the catalog type and
    immediately materializes a local .fctb pre-filled from the catalog's nominal
    geometry, linked to the new instance (the orchestration lives in
    ``sync.create_tool_from_catalog``; this class only renders and fires it)."""

    TITLE = "Catalog"
    HEADERS = ["Name", "Manufacturer", "Product code", "Geometry", "Source"]

    def __init__(self, window, client, tools_dir):
        super().__init__(window, client)
        self.tools_dir = tools_dir
        self._records_by_id = {}
        layout = QtGui.QVBoxLayout(self)
        hint = QtGui.QLabel(
            "Browse catalog records; select one and 'Create tool from catalog' to "
            "make an unbound tool in this library, pre-filled from the catalog's "
            "nominal geometry and linked to the new record.")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.tree = QtGui.QTreeWidget()
        self.tree.setHeaderLabels(self.HEADERS)
        self.tree.setRootIsDecorated(False)
        self.tree.setColumnWidth(0, 220)
        self.tree.setColumnWidth(1, 140)
        self.tree.setColumnWidth(2, 120)
        self.tree.setColumnWidth(3, 110)
        self.tree.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.itemSelectionChanged.connect(self._selection_changed)
        self.tree.itemDoubleClicked.connect(lambda *a: self._create_selected())
        layout.addWidget(self.tree)

        row = QtGui.QHBoxLayout()
        row.addStretch()
        self.create_button = QtGui.QPushButton("Create tool from catalog")
        self.create_button.setToolTip(
            "Create an unbound tool from the selected catalog record and write a "
            "local tool file pre-filled from its nominal geometry.")
        self.create_button.clicked.connect(self._create_selected)
        row.addWidget(self.create_button)
        layout.addLayout(row)
        self._selection_changed()

    def _tools_dir(self):
        return self.tools_dir

    def refresh(self):
        self.tree.clear()
        try:
            records = self.client.list_catalogs()
        except LoobricError as e:
            self.window.refresh_api_log()
            self._notify("✗ %s" % e)
            return
        self.window.refresh_api_log()
        self._records_by_id = {
            (r.get("internal") or {}).get("id"): r for r in records}
        rows = viewmodel.catalog_rows(records)
        if not rows:
            QtGui.QTreeWidgetItem(self.tree, ["(no catalog records)", "", "", "", ""])
            self._selection_changed()
            return
        for r in rows:
            item = QtGui.QTreeWidgetItem([
                r["name"], r["manufacturer"], r["product_code"],
                r["geometry"], r["source"]])
            item.setData(0, QtCore.Qt.UserRole, r["id"])
            self.tree.addTopLevelItem(item)
        self._selection_changed()

    def selected_record(self):
        rows = self.tree.selectedItems()
        if not rows:
            return None
        rid = rows[0].data(0, QtCore.Qt.UserRole)
        return self._records_by_id.get(rid) if rid else None

    def _selection_changed(self):
        self.create_button.setEnabled(self.selected_record() is not None)

    def _context_menu(self, point):
        at = self.tree.itemAt(point)
        if at is not None:
            self.tree.setCurrentItem(at)
        if self.selected_record() is None:
            return
        menu = QtGui.QMenu(self)
        inspect = menu.addAction("Inspect record (JSON)…")
        create = menu.addAction("Create tool from catalog")
        chosen = menu.exec_(self.tree.viewport().mapToGlobal(point))
        if chosen == inspect:
            self.window.inspect_selected()
        elif chosen == create:
            self._create_selected()

    def _create_selected(self):
        record = self.selected_record()
        if record is None:
            return
        try:
            result = sync.create_tool_from_catalog(
                self._tools_dir(), self.client, record, name=None,
                log=lambda m: App.Console.PrintMessage("Loobric: %s\n" % m))
        except LoobricError as e:
            self.window.refresh_api_log()
            QtGui.QMessageBox.warning(self, "Loobric", str(e))
            self._notify("✗ %s" % e)
            return
        self.window.refresh_api_log()
        self._notify("Created '%s' from catalog (unbound). Reload the CAM tool "
                     "library to see it in the editor." % result["basename"])
        self.refresh()
