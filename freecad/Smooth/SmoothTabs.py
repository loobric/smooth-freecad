# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
The three tabs of the Smooth window (SmoothDialog.py), plus shared widgets.

The information architecture is deliberately small (reboot Phase 3):

- **Sync** — the one CAM surface: a hierarchical ToolSet→tool plan of the local
  FreeCAD tool directory against the server, with a "needs attention" filter
  (attention is a *view* over the single plan, not a second screen) and per-row
  upload/download decisions. Tool/ToolSet management (rename, set type, delete,
  link a set to a machine) lives here as row actions; a changed row opens a
  side-by-side resolution.
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
from .client import SmoothError
from .viewmodel import (field_value, canonical as _canonical, short_id,
                        record_name, instance_shape, instance_diameter,
                        fmt_dia as _fmt_dia, cascade_choice, SKIP, LOCAL_WINS,
                        SERVER_WINS, resolution_rows, resolution_actions,
                        RESOLUTION_DECISION)


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
        self.setWindowTitle("Smooth API log")
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
        except SmoothError as e:
            self.window.refresh_api_log()
            if getattr(e, "status", None) == 409 and on_conflict is not None:
                on_conflict(e)
                return None
            QtGui.QMessageBox.warning(self, "Smooth", str(e))
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
    Set a direction on a folder node to cascade it; each row is still
    individually overridable. A 'needs attention' filter narrows to the items
    that aren't in sync. Right-click a row for management (rename, set type,
    delete, link a set to a machine); double-click a changed row to resolve it
    field-by-field. Nothing touches disk or server until Apply."""

    TITLE = "Sync"

    STATUS_ICON = {
        "unchanged": "✓", "push": "↑", "pull": "↓", "new_local": "↑",
        "new_server": "↓", "conflict": "⚠", "deleted_local": "✖",
        "deleted_server": "✖",
    }
    DIRECTIONS = ["leave unsynced", "upload local → server",
                  "download server → local"]
    ROW_CHOICES = {
        "deleted_local": ["leave unsynced", "delete on server too",
                          "restore from server"],
        "deleted_server": ["leave unsynced", "upload again (restore)",
                           "delete local file too"],
    }
    DECISIONS = {LOCAL_WINS: "push", SERVER_WINS: "pull"}
    DEFAULT_DIRECTION = {"push": LOCAL_WINS, "new_local": LOCAL_WINS,
                         "pull": SERVER_WINS, "new_server": SERVER_WINS,
                         "conflict": SKIP, "deleted_local": SKIP,
                         "deleted_server": SKIP}

    def __init__(self, window, client, tools_dir):
        super().__init__(window, client)
        self.tools_dir = tools_dir
        self.plan = {"items": [], "errors": []}
        self._row_widgets = {}
        self._shape_widgets = {}
        self._guessed = 0
        self._attention_only = False
        self._build()

    def _build(self):
        layout = QtGui.QVBoxLayout(self)

        top = QtGui.QHBoxLayout()
        hint = QtGui.QLabel(
            "Tip: use the dropdown on a 📁 tool-set row to set every tool inside "
            "at once; right-click a row to inspect, rename, set type, delete, or "
            "link a set to a machine.")
        hint.setWordWrap(True)
        top.addWidget(hint, stretch=1)
        # A checkable button (not a checkbox) so the on/off state is visible on
        # every FreeCAD theme — some render the checkbox indicator invisibly.
        self.attention_button = QtGui.QPushButton("Needs attention only")
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
        self.defaults_button = QtGui.QPushButton("All: Suggested")
        self.defaults_button.setToolTip(
            "Set every changed item to its suggested upload/download direction. "
            "Conflicts and deletions stay 'leave unsynced' — you decide those.")
        self.defaults_button.clicked.connect(lambda: self._set_all(default=True))
        buttons.addWidget(self.defaults_button)
        self.skip_button = QtGui.QPushButton("All: Skip")
        self.skip_button.setToolTip(
            "Set every item to 'leave unsynced' — Apply would then do nothing.")
        self.skip_button.clicked.connect(lambda: self._set_all(default=False))
        buttons.addWidget(self.skip_button)
        buttons.addStretch()
        self.apply_button = QtGui.QPushButton("Apply Selected")
        self.apply_button.clicked.connect(self._run_apply)
        self.apply_button.setEnabled(False)
        buttons.addWidget(self.apply_button)
        layout.addLayout(buttons)

    def _append(self, message):
        App.Console.PrintMessage("Smooth: %s\n" % message)
        QtGui.QApplication.processEvents()

    def _toggle_attention(self, on):
        self._attention_only = bool(on)
        self._render()

    # -- plan rendering ---------------------------------------------------

    def refresh(self):
        try:
            self.plan = sync.plan_sync(self.tools_dir, self.client,
                                       log=self._append)
        except SmoothError as e:
            self.window.refresh_api_log()
            self._notify("✗ planning failed: %s" % e)
            self._append("Planning failed: %s" % e)
            self.tree.clear()
            self.apply_button.setEnabled(False)
            return
        self.window.refresh_api_log()
        for error in self.plan["errors"]:
            self._append("⚠ %s" % error)
        self._render()

    def _render(self):
        """Build the tree from the view-model — the single source of 'what to
        show'. Called on refresh and when the attention filter toggles (no
        re-fetch)."""
        self.tree.clear()
        self._row_widgets = {}
        self._shape_widgets = {}
        self._guessed = 0
        model = viewmodel.sync_tree(self.plan["items"],
                                    attention_only=self._attention_only)
        for group in model["groups"]:
            icon = "📁" if group["kind"] == "library" else "📄"
            node = QtGui.QTreeWidgetItem(
                ["%s %s" % (icon, group["name"]), group["rollup"], "", ""])
            node.setToolTip(1, "Derived from this set's tools — the set itself is "
                               "never 'conflicted'.")
            self.tree.addTopLevelItem(node)
            child_keys = []
            if group["library_item"] is not None:
                self._add_row(node, group["library_item"], indent_self=True)
                child_keys.append(group["library_item"]["key"])
            for bit in group["members"]:
                self._add_row(node, bit)
                child_keys.append(bit["key"])
            self._attach_cascade(node, child_keys)
            node.setExpanded(True)

        pending = model["pending"]
        if pending:
            extra = (" Tool types for %d download(s) were pre-filled from their "
                     "names — review the 'Tool type' column." % self._guessed
                     ) if self._guessed else ""
            self._notify("Plan ready: %d item(s) need attention.%s" % (pending, extra))
        else:
            self._notify("Everything is in sync.")
        # The bulk-direction and apply controls only mean something when there is
        # at least one changed item.
        for b in (self.apply_button, self.defaults_button, self.skip_button):
            b.setEnabled(pending > 0)

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
        row = QtGui.QTreeWidgetItem([label, status, "", self._tool_type_text(item)])
        row.setToolTip(1, item["detail"] or info["hint"])
        row.setData(0, QtCore.Qt.UserRole, item["key"])
        parent.addChild(row)
        # In-sync rows stay SELECTABLE (so they can be inspected / right-clicked);
        # they just get no direction control.
        if item["action"] == "unchanged":
            return
        combo = QtGui.QComboBox()
        combo.addItems(self.ROW_CHOICES.get(item["action"], self.DIRECTIONS))
        combo.setCurrentIndex(self.DEFAULT_DIRECTION[item["action"]])
        if item["action"] not in self.ROW_CHOICES:
            if item["path"] is None:
                combo.model().item(LOCAL_WINS).setEnabled(False)   # nothing local
            if item["record"] is None:
                combo.model().item(SERVER_WINS).setEnabled(False)  # nothing on server
        self.tree.setItemWidget(row, 2, combo)
        self._row_widgets[item["key"]] = combo
        if sync.needs_shape_choice(item):
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

    def _attach_cascade(self, node, child_keys):
        """A folder-node menu that pushes one direction onto every child row
        (where it applies). It acts like a menu, not a state: it resets after use."""
        actionable = [k for k in child_keys if k in self._row_widgets]
        if not actionable:
            return
        combo = QtGui.QComboBox()
        combo.addItems(["Set all in set…"] + self.DIRECTIONS)
        combo.activated.connect(
            lambda i, keys=actionable, c=combo: self._cascade(i, keys, c))
        self.tree.setItemWidget(node, 2, combo)

    def _cascade(self, selected, child_keys, combo):
        combo.setCurrentIndex(0)            # menu, not state
        if selected == 0:
            return
        node_index = selected - 1           # 0/1/2 == SKIP/LOCAL_WINS/SERVER_WINS
        items_by_key = {i["key"]: i for i in self.plan["items"]}
        for key in child_keys:
            child = self._row_widgets.get(key)
            item = items_by_key.get(key)
            if child is None or item is None:
                continue
            target = cascade_choice(
                node_index, has_local=item["path"] is not None,
                has_server=item["record"] is not None,
                is_deletion=item["action"] in ("deleted_local", "deleted_server"))
            if target is None:
                continue
            model_item = child.model().item(target)
            if model_item is not None and model_item.isEnabled():
                child.setCurrentIndex(target)

    def _set_all(self, default):
        items_by_key = {i["key"]: i for i in self.plan["items"]}
        for key, combo in self._row_widgets.items():
            if default:
                combo.setCurrentIndex(self.DEFAULT_DIRECTION[items_by_key[key]["action"]])
            else:
                combo.setCurrentIndex(SKIP)

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
        menu.addSeparator()
        rename = menu.addAction("Rename…")
        rename.setEnabled(record is not None)
        set_type = None
        if not is_set:
            set_type = menu.addAction("Set tool type…")
            set_type.setEnabled(record is not None)
        link = None
        if is_set:
            link = menu.addAction("Link to machine…")
            link.setEnabled(record is not None)
        menu.addSeparator()
        delete = menu.addAction("Delete on server…")
        delete.setEnabled(record is not None)
        if record is None:
            menu.addAction("(upload first to manage on the server)").setEnabled(False)

        chosen = menu.exec_(self.tree.viewport().mapToGlobal(point))
        if chosen is None:
            return
        if chosen == inspect:
            self.window.inspect_selected()
        elif chosen == rename:
            self._rename(item)
        elif set_type is not None and chosen == set_type:
            self._set_type(item)
        elif link is not None and chosen == link:
            self._link_to_machine(item)
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

    def _link_to_machine(self, item):
        record = item["record"]
        sid = record["internal"]["id"]
        try:
            machines = self.client.list_machines()
        except SmoothError as e:
            self.window.refresh_api_log()
            QtGui.QMessageBox.warning(self, "Smooth", str(e))
            return
        self.window.refresh_api_log()
        if not machines:
            QtGui.QMessageBox.information(
                self, "Link to machine", "No machines on the server yet.")
            return
        labels = ["%s  [%s]" % (record_name(m), short_id(m)) for m in machines]
        choice, ok = QtGui.QInputDialog.getItem(
            self, "Link to machine",
            "Link '%s' to which machine? (its member numbers are then inherited "
            "from that machine's tool table)" % record_name(record),
            labels, 0, False)
        if not ok:
            return
        mid = machines[labels.index(choice)]["internal"]["id"]
        self.act(lambda: self.client.link_set_machine(sid, mid),
                 success="Linked to the machine.")

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
        through the same upload/download write paths as Apply."""
        item = self._selected_item()
        if not item or item["action"] == "unchanged":
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
        except SmoothError as e:
            self.window.refresh_api_log()
            QtGui.QMessageBox.warning(self, "Smooth", str(e))
            self._notify("✗ %s" % e)
            return
        self.window.refresh_api_log()
        if summary["errors"]:
            QtGui.QMessageBox.warning(self, "Smooth", "\n".join(summary["errors"]))
        self._notify("Resolved '%s' — %d uploaded, %d downloaded."
                     % (item.get("name"), summary["pushed"], summary["pulled"]))
        self.refresh()

    # -- apply ------------------------------------------------------------

    def _collect_decisions(self):
        return {key: self.DECISIONS[combo.currentIndex()]
                for key, combo in self._row_widgets.items()
                if combo.currentIndex() in self.DECISIONS}

    def _collect_shapes(self):
        return {key: combo.currentText()
                for key, combo in self._shape_widgets.items()}

    def _run_apply(self):
        decisions = self._collect_decisions()
        if not decisions:
            self._notify("Nothing selected to apply.")
            return
        self.apply_button.setEnabled(False)
        try:
            summary = sync.apply_sync(self.tools_dir, self.client, self.plan,
                                      decisions, shapes=self._collect_shapes(),
                                      log=self._append)
        except SmoothError as e:
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
        except SmoothError as e:
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
        except SmoothError as e:
            self.window.refresh_api_log()
            QtGui.QMessageBox.warning(self, "Smooth", str(e))
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
        except SmoothError as e:
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
