# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
Tab widgets and debug panels for the tabbed Smooth window (SmoothDialog.py).

Mirrors the smooth-core web UI: a **Sync** tab (plan/apply of the local tool
dir, with bulk node-level actions), and operator-lane tabs — **Inbox**,
**Tools**, **Tool Sets**, **Coverage** (a set's tools vs. its linked machine's
tool table), **Machines** (with their tool tables), **Audit** —
plus two shared debug widgets: a raw sectioned-record **RecordInspector** and a
live **ApiLogPanel** fed by ``SmoothClient.call_log``.

All sync/plan logic stays in the headless-tested modules (sync.py, mapping.py,
client.py); these classes only render and collect decisions / fire actions.
The one piece of pure logic that lives here — ``cascade_choice`` — is unit
tested in tests/test_sync_cascade.py.
"""
import json

import FreeCAD as App
from PySide import QtGui, QtCore

from . import sync, mapping
from .client import SmoothError
from .uihelpers import (field_value, field_source, canonical as _canonical,
                        short_id, record_name, instance_shape,
                        instance_diameter, fmt_dia as _fmt_dia,
                        cascade_choice, SKIP, LOCAL_WINS, SERVER_WINS,
                        coverage_is_applicable,
                        content_attention_rows, coverage_attention_rows,
                        needs_attention_rows, library_rollup,
                        library_rollup_line, resolution_rows,
                        resolution_actions, RESOLUTION_DECISION,
                        SEV_CRITICAL, SEV_WARNING, SEV_INFO)


# Severity -> (background, foreground, bold). Shared by the Needs-Attention and
# Coverage views so the same band looks the same everywhere. Each entry is
# (background, foreground, bold). We set BOTH colors deliberately: a light
# background alone leaves the row's text at the theme default, which is light on
# FreeCAD's dark themes — i.e. white-on-white. Pairing each light tint with a
# dark foreground keeps the row legible on light AND dark themes.
_FG_ON_TINT = QtGui.QColor(33, 33, 33)
TINT_WARNING = QtGui.QColor(255, 235, 190)
SEV_STYLE = {
    SEV_CRITICAL: (QtGui.QColor(255, 205, 205), _FG_ON_TINT, True),
    SEV_WARNING: (TINT_WARNING, _FG_ON_TINT, False),
    SEV_INFO: (QtGui.QColor(238, 238, 238), _FG_ON_TINT, False),
}


# ---------------------------------------------------------------------------
# Shared debug widgets
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
        # cheap live refresh while open
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
    and the 'what record is selected' hook the window's Inspect button uses."""

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
            if e.status == 409 and on_conflict is not None:
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
# Sync tab — plan/apply with bulk node-level actions
# ---------------------------------------------------------------------------

class SyncTab(_Tab):
    """The Libraries view: hierarchical browsing of every library and its tools,
    each library labelled with a DERIVED rollup of its contents (✓ synced ·
    ↑ local-only · ↓ server-only · ⚠ conflicts). Set a direction on a folder node
    to cascade it to every tool inside; each row is still individually
    overridable. Nothing touches disk or server until Apply.

    A library is organization, not a sync object — its rollup *summarizes* its
    tools; the library is never itself 'conflicted'."""

    TITLE = "Libraries"

    ACTION_LABELS = {
        "unchanged": "in sync", "push": "changed here",
        "pull": "changed on server", "new_local": "new here",
        "new_server": "new on server", "conflict": "changed on BOTH sides",
        "deleted_local": "deleted here", "deleted_server": "deleted on server",
    }
    ACTION_ICON = {
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
        self._group_children = {}
        self._guessed = 0
        self._build()

    def _build(self):
        layout = QtGui.QVBoxLayout(self)
        hint = QtGui.QLabel(
            "Tip: use the dropdown on a 📁 folder row to set every tool inside "
            "at once — then fine-tune individual rows.")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.tree = QtGui.QTreeWidget()
        self.tree.setHeaderLabels(["Item", "Status", "Action", "Tool type"])
        self.tree.setColumnWidth(0, 260)
        self.tree.setColumnWidth(1, 160)
        self.tree.setColumnWidth(2, 180)
        self.tree.itemSelectionChanged.connect(self._show_diff)
        layout.addWidget(self.tree, stretch=3)

        self.diff_pane = QtGui.QTextEdit()
        self.diff_pane.setReadOnly(True)
        self.diff_pane.setMaximumHeight(120)
        self.diff_pane.setPlaceholderText(
            "Select a changed item to see exactly which fields differ, each "
            "attributed to the side that made the change.")
        layout.addWidget(self.diff_pane, stretch=1)

        self.log_box = QtGui.QGroupBox("Log")
        self.log_box.setCheckable(True)
        self.log_box.setChecked(False)
        log_layout = QtGui.QVBoxLayout(self.log_box)
        self.log = QtGui.QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(110)
        log_layout.addWidget(self.log)
        self.log.setVisible(False)
        self.log_box.toggled.connect(self.log.setVisible)
        layout.addWidget(self.log_box, stretch=1)

        buttons = QtGui.QHBoxLayout()
        refresh = QtGui.QPushButton("Refresh Plan")
        refresh.clicked.connect(self.refresh)
        buttons.addWidget(refresh)
        defaults = QtGui.QPushButton("All: Suggested")
        defaults.setToolTip("Set every item to its suggested direction "
                            "(conflicts stay 'leave unsynced').")
        defaults.clicked.connect(lambda: self._set_all(default=True))
        buttons.addWidget(defaults)
        skip = QtGui.QPushButton("All: Skip")
        skip.clicked.connect(lambda: self._set_all(default=False))
        buttons.addWidget(skip)
        buttons.addStretch()
        self.apply_button = QtGui.QPushButton("Apply Selected")
        self.apply_button.clicked.connect(self._run_apply)
        self.apply_button.setEnabled(False)
        buttons.addWidget(self.apply_button)
        layout.addLayout(buttons)

    def _append(self, message):
        self.log.append(message)
        App.Console.PrintMessage("Smooth: %s\n" % message)
        QtGui.QApplication.processEvents()

    # -- plan rendering ---------------------------------------------------

    def refresh(self):
        self.tree.clear()
        self._row_widgets = {}
        self._shape_widgets = {}
        self._group_children = {}
        self._guessed = 0
        try:
            self.plan = sync.plan_sync(self.tools_dir, self.client,
                                       log=self._append)
        except SmoothError as e:
            self.window.refresh_api_log()
            self._notify("✗ planning failed: %s" % e)
            self._append("Planning failed: %s" % e)
            self.apply_button.setEnabled(False)
            return
        self.window.refresh_api_log()
        for error in self.plan["errors"]:
            self._append("⚠ %s" % error)

        items = self.plan["items"]
        libraries = [i for i in items if i["kind"] == "library"]
        bits = [i for i in items if i["kind"] == "bit"]
        bits_by_group = {}
        for bit in bits:
            bits_by_group.setdefault(bit.get("group"), []).append(bit)

        pending = 0
        shown = set()
        for library in sorted(libraries, key=lambda i: i["name"]):
            members = sorted(bits_by_group.get(library["group"], []),
                             key=lambda i: i["name"])
            rollup = library_rollup_line(library_rollup(members))
            node = QtGui.QTreeWidgetItem(
                ["📁 %s" % library["name"], rollup, "", ""])
            node.setToolTip(1, "Derived from this library's tools — the library "
                               "itself is never 'conflicted'.")
            self.tree.addTopLevelItem(node)
            child_keys = []
            pending += self._add_row(node, library, indent_self=True)
            child_keys.append(library["key"])
            for bit in members:
                pending += self._add_row(node, bit)
                child_keys.append(bit["key"])
                shown.add(bit["key"])
            self._attach_cascade(node, child_keys)
            node.setExpanded(True)
        loose = [b for b in sorted(bits, key=lambda i: i["name"])
                 if b["key"] not in shown]
        if loose:
            # Unassigned toolbits (in no library) stay visible with their own
            # rollup, never hidden.
            rollup = library_rollup_line(library_rollup(loose))
            node = QtGui.QTreeWidgetItem(
                ["📄 Not in any library", rollup, "", ""])
            self.tree.addTopLevelItem(node)
            child_keys = []
            for bit in loose:
                pending += self._add_row(node, bit)
                child_keys.append(bit["key"])
            self._attach_cascade(node, child_keys)
            node.setExpanded(True)

        if pending:
            extra = (" Tool types for %d download(s) were pre-filled from their "
                     "names — review the 'Tool type' column." % self._guessed
                     ) if self._guessed else ""
            self._notify("Plan ready: %d item(s) need attention.%s" % (pending, extra))
            self._append("Plan ready: %d item(s) need attention. Suggested "
                         "directions are pre-selected.%s" % (pending, extra))
        else:
            self._notify("Everything is in sync.")
            self._append("Everything is in sync.")
        self.apply_button.setEnabled(pending > 0)

    def _add_row(self, parent, item, indent_self=False):
        label = "(this library)" if indent_self else item["name"]
        status = "%s %s" % (self.ACTION_ICON.get(item["action"], ""),
                            self.ACTION_LABELS[item["action"]])
        row = QtGui.QTreeWidgetItem([label, status, ""])
        row.setToolTip(1, item["detail"])
        row.setData(0, QtCore.Qt.UserRole, item["key"])
        parent.addChild(row)
        if item["action"] == "unchanged":
            row.setDisabled(True)
            return 0
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
                "FreeCAD can't change a bit's type after creation, so correct "
                "it here if the guess is wrong.")
            self.tree.setItemWidget(row, 3, shape_combo)
            self._shape_widgets[item["key"]] = shape_combo
        return 1

    def _attach_cascade(self, node, child_keys):
        """A folder-node menu that pushes one direction onto every child row
        (where it applies). It acts like a menu, not a state: it resets to the
        placeholder after each use."""
        actionable = [k for k in child_keys if k in self._row_widgets]
        if not actionable:
            return
        combo = QtGui.QComboBox()
        combo.addItems(["Set all in folder…"] + self.DIRECTIONS)
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

    def _show_diff(self):
        selected = self.tree.selectedItems()
        if not selected:
            return
        key = selected[0].data(0, QtCore.Qt.UserRole)
        item = next((i for i in self.plan["items"] if i["key"] == key), None)
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
        selected = self.tree.selectedItems()
        if not selected:
            return None
        key = selected[0].data(0, QtCore.Qt.UserRole)
        item = next((i for i in self.plan["items"] if i["key"] == key), None)
        return item.get("record") if item else None

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
            self._append("Reload the tool library (or restart FreeCAD) to see "
                         "downloaded changes in the editor.")
        self.refresh()


# ---------------------------------------------------------------------------
# A simple flat list/table tab base (Inbox, Tools, Tool Sets, Audit)
# ---------------------------------------------------------------------------

class _ListTab(_Tab):
    """A tree used as a table, with a row of action buttons that operate on the
    selected row. Subclasses set HEADERS, fill rows in ``populate``, and build
    their action buttons in ``build_actions``."""

    HEADERS = []
    COLUMN_WIDTHS = {}

    def __init__(self, window, client):
        super().__init__(window, client)
        layout = QtGui.QVBoxLayout(self)
        self.tree = QtGui.QTreeWidget()
        self.tree.setHeaderLabels(self.HEADERS)
        self.tree.setRootIsDecorated(False)
        for col, width in self.COLUMN_WIDTHS.items():
            self.tree.setColumnWidth(col, width)
        self.tree.itemSelectionChanged.connect(self._selection_changed)
        self.tree.itemDoubleClicked.connect(lambda *a: self.window.inspect_selected())
        layout.addWidget(self.tree)
        self.button_row = QtGui.QHBoxLayout()
        self.build_actions(self.button_row)
        self.button_row.addStretch()
        refresh = QtGui.QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        self.button_row.addWidget(refresh)
        layout.addLayout(self.button_row)
        self._selection_changed()

    def build_actions(self, row):
        pass

    def populate(self):
        return []

    def refresh(self):
        self.tree.clear()
        try:
            self.populate()
        except SmoothError as e:
            self.window.refresh_api_log()
            self._notify("✗ %s" % e)
            return
        self.window.refresh_api_log()
        self._selection_changed()

    def _add(self, columns, record=None):
        item = QtGui.QTreeWidgetItem([str(c) for c in columns])
        if record is not None:
            item.setData(0, QtCore.Qt.UserRole, record)
        self.tree.addTopLevelItem(item)
        return item

    def selected_item(self):
        rows = self.tree.selectedItems()
        return rows[0] if rows else None

    def selected_record(self):
        item = self.selected_item()
        return item.data(0, QtCore.Qt.UserRole) if item else None

    def _selection_changed(self):
        pass


# ---------------------------------------------------------------------------
# Inbox tab
# ---------------------------------------------------------------------------

class InboxTab(_ListTab):
    TITLE = "Inbox"
    HEADERS = ["Slot", "Slot description", "Proposed tool", "Confidence"]
    COLUMN_WIDTHS = {0: 120, 1: 220, 2: 200}

    def build_actions(self, row):
        self.same = QtGui.QPushButton("Same tool (confirm)")
        self.same.clicked.connect(self._confirm)
        row.addWidget(self.same)
        self.diff = QtGui.QPushButton("Different (reject)")
        self.diff.clicked.connect(self._reject)
        row.addWidget(self.diff)
        self.adopt = QtGui.QPushButton("Adopt as new tool")
        self.adopt.clicked.connect(self._adopt)
        row.addWidget(self.adopt)

    def populate(self):
        proposals = self.client.list_inbox()
        if not proposals:
            self._add(["—", "inbox empty: no unbound slots awaiting a decision",
                       "", ""])
            return
        for p in proposals:
            entry = p.get("entry") or {}
            inst = p.get("proposed_instance") or {}
            tnum = entry.get("tool_number")
            conf = p.get("confidence")
            self._add([
                "T%s" % tnum if tnum is not None else "?",
                p.get("reason") or "—",
                "%s (%s)" % (inst.get("name") or "?", _fmt_dia(inst.get("diameter")))
                if inst.get("id") else "(no match)",
                "%d%%" % round(conf * 100) if isinstance(conf, (int, float)) else "—",
            ], record=p)

    def _proposal(self):
        rec = self.selected_record()
        return rec if isinstance(rec, dict) and rec.get("id") else None

    def _confirm(self):
        p = self._proposal()
        if not p:
            return
        self.act(lambda: self.client.confirm_proposal(p["id"]),
                 success="Bound the proposed tool into the slot.")

    def _reject(self):
        p = self._proposal()
        if not p:
            return
        self.act(lambda: self.client.reject_proposal(p["id"]),
                 success="Rejected — that pairing won't be proposed again.")

    def _adopt(self):
        p = self._proposal()
        if not p:
            return
        slot_id = (p.get("slot") or {}).get("id")
        if not slot_id:
            return
        name, ok = QtGui.QInputDialog.getText(
            self, "Adopt as new tool",
            "Name for the new tool (blank = use the slot's description):")
        if not ok:
            return
        self.act(lambda: self.client.adopt_entry(slot_id, name=name or None),
                 success="Created a new tool from the slot and installed it.")

    def _selection_changed(self):
        has = self._proposal() is not None
        for b in (self.same, self.diff, self.adopt):
            b.setEnabled(has)


# ---------------------------------------------------------------------------
# Tools tab (instances)
# ---------------------------------------------------------------------------

class ToolsTab(_ListTab):
    TITLE = "Tools"
    HEADERS = ["Name", "Id", "Type", "Diameter", "Installed"]
    COLUMN_WIDTHS = {0: 220, 1: 90, 2: 120, 3: 90}

    def build_actions(self, row):
        self.rename = QtGui.QPushButton("Rename")
        self.rename.clicked.connect(self._rename)
        row.addWidget(self.rename)
        self.settype = QtGui.QPushButton("Set type")
        self.settype.clicked.connect(self._set_type)
        row.addWidget(self.settype)
        self.delete = QtGui.QPushButton("Delete")
        self.delete.clicked.connect(self._delete)
        row.addWidget(self.delete)

    def populate(self):
        instances = self.client.list_instances()
        # where is each instance installed? cross-reference the slots.
        installed = {}
        try:
            for entry in self.client.list_entries():
                bound = field_value(_canonical(entry, "bound_instance_id"))
                if bound:
                    tnum = field_value(_canonical(entry, "tool_number"))
                    machine = (entry.get("internal") or {}).get("machine_id", "")
                    installed.setdefault(bound, []).append(
                        "T%s@%s" % (tnum, (machine or "?")[:8]))
        except SmoothError:
            pass  # the tool list still renders without install locations
        if not instances:
            self._add(["(no tools on the server yet)", "", "", "", ""])
            return
        for record in sorted(instances, key=record_name):
            rid = (record.get("internal") or {}).get("id")
            self._add([
                record_name(record), short_id(record),
                instance_shape(record) or "—",
                _fmt_dia(instance_diameter(record)),
                ", ".join(installed.get(rid, [])) or "—",
            ], record=record)

    def _record(self):
        rec = self.selected_record()
        return rec if isinstance(rec, dict) and rec.get("internal") else None

    def _rename(self):
        rec = self._record()
        if not rec:
            return
        new, ok = QtGui.QInputDialog.getText(self, "Rename tool", "Name:",
                                             text=record_name(rec))
        if not ok or not new:
            return
        rid = rec["internal"]["id"]
        self.act(lambda: self.client.assert_instance(rid, "name", new),
                 success="Renamed.")

    def _set_type(self):
        rec = self._record()
        if not rec:
            return
        current = instance_shape(rec) or mapping.DEFAULT_SHAPE
        idx = (mapping.FREECAD_SHAPES.index(current)
               if current in mapping.FREECAD_SHAPES else 0)
        shape, ok = QtGui.QInputDialog.getItem(
            self, "Set tool type", "Type (asserts geometry.shape):",
            mapping.FREECAD_SHAPES, idx, False)
        if not ok:
            return
        rid = rec["internal"]["id"]
        self.act(lambda: self.client.assert_instance(rid, "geometry.shape", shape),
                 success="Type asserted.")

    def _delete(self):
        rec = self._record()
        if not rec:
            return
        rid = rec["internal"]["id"]
        self.act(lambda: self.client.delete_instance(rid),
                 confirm=("Delete tool",
                          "Delete '%s'? Any slot holding it is unbound first."
                          % record_name(rec)),
                 success="Deleted.")

    def _selection_changed(self):
        has = self._record() is not None
        for b in (self.rename, self.settype, self.delete):
            b.setEnabled(has)


# ---------------------------------------------------------------------------
# Tool Sets tab
# ---------------------------------------------------------------------------

class ToolSetsTab(_ListTab):
    TITLE = "Tool Sets"
    HEADERS = ["Name", "Id", "Members", "Linked machine"]
    COLUMN_WIDTHS = {0: 240, 1: 90, 2: 90}

    def build_actions(self, row):
        self.rename = QtGui.QPushButton("Rename")
        self.rename.clicked.connect(self._rename)
        row.addWidget(self.rename)
        self.reconcile = QtGui.QPushButton("Reconcile")
        self.reconcile.setToolTip("Inherit member numbers from the linked "
                                  "machine's slots (machine-bound sets only).")
        self.reconcile.clicked.connect(self._reconcile)
        row.addWidget(self.reconcile)
        self.delete = QtGui.QPushButton("Delete")
        self.delete.clicked.connect(self._delete)
        row.addWidget(self.delete)

    def populate(self):
        sets = self.client.list_sets()
        if not sets:
            self._add(["(no tool sets on the server yet)", "", "", ""])
            return
        for record in sorted(sets, key=record_name):
            members = (record.get("canonical") or {}).get("members") or []
            machine = field_value(_canonical(record, "machine_id"))
            self._add([record_name(record), short_id(record), len(members),
                       (machine or "—")], record=record)

    def _record(self):
        rec = self.selected_record()
        return rec if isinstance(rec, dict) and rec.get("internal") else None

    def _rename(self):
        rec = self._record()
        if not rec:
            return
        new, ok = QtGui.QInputDialog.getText(self, "Rename tool set", "Name:",
                                             text=record_name(rec))
        if not ok or not new:
            return
        sid = rec["internal"]["id"]
        self.act(lambda: self.client.assert_set(sid, "name", new), success="Renamed.")

    def _reconcile(self):
        rec = self._record()
        if not rec:
            return
        sid = rec["internal"]["id"]
        result = self.act(lambda: self.client.reconcile_set(sid),
                          success="Reconciled.")
        if result and result.get("unreconciled"):
            QtGui.QMessageBox.information(
                self, "Reconcile",
                "These members have no slot on the linked machine:\n%s"
                % "\n".join(result["unreconciled"]))

    def _delete(self):
        rec = self._record()
        if not rec:
            return
        sid = rec["internal"]["id"]
        self.act(lambda: self.client.delete_set(sid),
                 confirm=("Delete tool set",
                          "Delete the set '%s'? The member tools are NOT deleted."
                          % record_name(rec)),
                 success="Deleted.")

    def _selection_changed(self):
        has = self._record() is not None
        for b in (self.rename, self.reconcile, self.delete):
            b.setEnabled(has)


# ---------------------------------------------------------------------------
# Per-object resolution view (side-by-side field comparison)
# ---------------------------------------------------------------------------

class ResolutionDialog(QtGui.QDialog):
    """One object, one resolution. Shows the per-field side-by-side comparison
    (server side annotated with canonical provenance) and offers Keep Local /
    Keep Server / Skip. The chosen key is read back via :meth:`get_choice`.

    All comparison content comes from the pure ``resolution_rows`` helper; this
    class only renders it — so the comparison logic stays headless-testable."""

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
                str(r["field"]),
                repr(r["local"]),
                repr(r["server"]),
                r["server_source"] or "—",
            ])
            # Tint the side that moved away from sync.
            changed = r.get("changed_by")
            if changed in ("local", "both"):
                item.setBackground(1, TINT_WARNING)
                item.setForeground(1, _FG_ON_TINT)
            if changed in ("server", "both"):
                item.setBackground(2, TINT_WARNING)
                item.setForeground(2, _FG_ON_TINT)
            table.addTopLevelItem(item)
        if not rows:
            note = ("No field-level differences are recorded for this item "
                    "(it may be a whole-object create/delete). Choose which "
                    "side to keep.")
            layout.addWidget(QtGui.QLabel(note))
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
# Needs Attention — the exceptions-first primary view (issue #9)
# ---------------------------------------------------------------------------

class NeedsAttentionTab(_Tab):
    """The primary, exceptions-first view. Aggregates the two exception axes —
    CAM content (per-object Local-only / Server-only / Modified / Conflict, from
    the sync plan) and MACHINE COVERAGE (absent_on_machine / number_mismatch /
    collisions, from each linked set's /coverage) — into one worst-first stream.
    Healthy, in-sync objects stay quiet; an empty list means all is well.

    The standalone Coverage tab folds in here: coverage is just one exception
    stream, not a separate tab. Selecting a CAM-content row opens the per-object
    resolution view; coverage rows are physical/operator-lane and route the user
    to where they're fixed (link/Machines), since there is no library write that
    loads a tool onto a machine.

    All derivation/aggregation lives in pure uihelpers (headless-tested); this
    class only fetches, renders, and dispatches resolutions."""

    TITLE = "Needs Attention"

    def __init__(self, window, client, tools_dir):
        super().__init__(window, client)
        self.tools_dir = tools_dir
        self._plan = {"items": [], "errors": []}
        self._rows = []            # row dicts shown, by tree index
        self._unlinked = []        # (set_id, set_name) of sets without a machine
        layout = QtGui.QVBoxLayout(self)

        self.summary = QtGui.QLabel("")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        self.tree = QtGui.QTreeWidget()
        self.tree.setHeaderLabels(["Type", "Tool", "Where", "Status"])
        self.tree.setRootIsDecorated(False)
        self.tree.setColumnWidth(0, 80)
        self.tree.setColumnWidth(1, 220)
        self.tree.setColumnWidth(2, 160)
        self.tree.itemSelectionChanged.connect(self._selection_changed)
        self.tree.itemDoubleClicked.connect(lambda *_: self._resolve())
        layout.addWidget(self.tree, stretch=1)

        buttons = QtGui.QHBoxLayout()
        refresh = QtGui.QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        buttons.addWidget(refresh)
        self.resolve_button = QtGui.QPushButton("Resolve…")
        self.resolve_button.setToolTip(
            "Open the side-by-side resolution for the selected CAM object.")
        self.resolve_button.clicked.connect(self._resolve)
        self.resolve_button.setEnabled(False)
        buttons.addWidget(self.resolve_button)
        self.link_button = QtGui.QPushButton("Link set to machine…")
        self.link_button.setToolTip(
            "Link a tool set to a machine so its coverage can be checked.")
        self.link_button.clicked.connect(self._link)
        buttons.addWidget(self.link_button)
        buttons.addStretch()
        layout.addLayout(buttons)

    # -- data --------------------------------------------------------------

    def refresh(self):
        self.tree.clear()
        self._rows = []
        self._unlinked = []
        try:
            self._plan = sync.plan_sync(self.tools_dir, self.client)
        except SmoothError as e:
            self.window.refresh_api_log()
            self.summary.setText("✗ %s" % e)
            return
        items = self._plan["items"]

        # CAM-content axis (library): one row per object, synced ones dropped.
        content = content_attention_rows(items)

        # Names for coverage rows, harvested from the plan's bit records (no
        # extra request). Sets likewise come from the plan's library items.
        names = {}
        sets = []
        for it in items:
            rec = it.get("record")
            if not rec:
                continue
            rid = (rec.get("internal") or {}).get("id")
            if it["kind"] == "bit" and rid:
                names[rid] = it["name"]
            elif it["kind"] == "library" and rid:
                sets.append((rid, it["name"]))

        # Machine-coverage axis (control): per linked set, the actionable rows.
        coverage = []
        for sid, sname in sets:
            try:
                cov = self.client.get_set_coverage(sid)
            except SmoothError as e:
                self.summary.setText("✗ coverage for %s: %s" % (sname, e))
                continue
            if coverage_is_applicable(cov):
                coverage.extend(coverage_attention_rows(
                    cov, set_name=sname, set_id=sid, names=names))
            else:
                self._unlinked.append((sid, sname))
        self.window.refresh_api_log()

        self._rows = needs_attention_rows(content, coverage)
        for row in self._rows:
            self._add_row(row)

        if self._rows:
            self.summary.setText(
                "%d item(s) need attention — worst first. Double-click a CAM "
                "object to resolve it." % len(self._rows))
        else:
            self.summary.setText("✓ Nothing needs attention — everything is in "
                                 "sync and on its machines.")
        if self._unlinked:
            self.summary.setText(self.summary.text() + "  (%d set(s) not linked "
                                 "to a machine — coverage unchecked.)"
                                 % len(self._unlinked))

    def _add_row(self, row):
        axis = row.get("axis")
        if axis == "coverage":
            where = "set: %s" % (row.get("set_name") or "?")
            tnum = row.get("tnum")
            obj = row.get("name") or "?"
            if tnum is not None:
                obj = "T%s  %s" % (tnum, obj)
            status = row.get("label") or row.get("status") or ""
            if row.get("collides") and row.get("collides_with"):
                status = "%s (with T%s)" % (
                    status, ", T".join(str(t) for t in row["collides_with"]))
            axis_label = "Machine"
        else:
            where = row.get("group") or "(unassigned)"
            obj = row.get("name") or "?"
            status = row.get("label") or ""
            axis_label = "Library"
        item = QtGui.QTreeWidgetItem([axis_label, obj, where, status])
        item.setToolTip(3, row.get("hint") or "")
        # Stash this row's index into self._rows so the resolution dispatch can
        # recover the full row dict from the selected tree item.
        item.setData(0, QtCore.Qt.UserRole, self.tree.topLevelItemCount())
        style = SEV_STYLE.get(row.get("severity"))
        if style:
            bg, fg, bold = style
            for col in range(4):
                item.setBackground(col, bg)
                item.setForeground(col, fg)
            if bold:
                font = item.font(3)
                font.setBold(True)
                item.setFont(1, font)
                item.setFont(3, font)
        self.tree.addTopLevelItem(item)

    # -- resolution / actions ----------------------------------------------

    def _selected_row(self):
        selected = self.tree.selectedItems()
        if not selected:
            return None
        idx = selected[0].data(0, QtCore.Qt.UserRole)
        if idx is None or idx >= len(self._rows):
            return None
        return self._rows[idx]

    def _selection_changed(self):
        row = self._selected_row()
        self.resolve_button.setEnabled(
            bool(row) and row.get("axis") == "content")

    def _resolve(self):
        row = self._selected_row()
        if not row:
            return
        if row.get("axis") == "coverage":
            QtGui.QMessageBox.information(
                self, "Coverage exception",
                "%s\n\nThis is a machine-coverage issue, resolved physically "
                "(load/order the tool) or on the Machines tab (bind/renumber "
                "slots) — not by a library upload/download." % (row.get("hint") or ""))
            return
        choice = ResolutionDialog(row, parent=self).get_choice()
        if not choice:
            return
        decision = RESOLUTION_DECISION.get(choice, "skip")
        if decision == "skip":
            self._notify("Skipped %s." % row.get("name"))
            return
        decisions = {row["key"]: decision}
        try:
            summary = sync.apply_sync(self.tools_dir, self.client, self._plan,
                                      decisions)
        except SmoothError as e:
            self.window.refresh_api_log()
            QtGui.QMessageBox.warning(self, "Smooth", str(e))
            self._notify("✗ %s" % e)
            return
        self.window.refresh_api_log()
        if summary["errors"]:
            QtGui.QMessageBox.warning(self, "Smooth",
                                      "\n".join(summary["errors"]))
        self._notify("Resolved '%s' — %d uploaded, %d downloaded."
                     % (row.get("name"), summary["pushed"], summary["pulled"]))
        self.refresh()

    def _link(self):
        """Link an unlinked set to a machine so its coverage becomes checkable.
        (Carried over from the folded-in Coverage tab; an operator-lane
        machine_id assert, not an invented endpoint.)"""
        if not self._unlinked:
            QtGui.QMessageBox.information(
                self, "Link set to machine",
                "Every tool set is already linked to a machine (or there are "
                "no sets yet).")
            return
        set_labels = ["%s  [%s]" % (name, sid[:8])
                      for sid, name in self._unlinked]
        choice, ok = QtGui.QInputDialog.getItem(
            self, "Link set to machine", "Which set?", set_labels, 0, False)
        if not ok:
            return
        sid = self._unlinked[set_labels.index(choice)][0]
        try:
            machines = self.client.list_machines()
        except SmoothError as e:
            self.window.refresh_api_log()
            QtGui.QMessageBox.warning(self, "Smooth", str(e))
            return
        self.window.refresh_api_log()
        if not machines:
            QtGui.QMessageBox.information(
                self, "Link set to machine",
                "No machines on the server yet to link to.")
            return
        labels = ["%s  [%s]" % (record_name(m), short_id(m)) for m in machines]
        mchoice, ok = QtGui.QInputDialog.getItem(
            self, "Link set to machine",
            "Map this set onto which machine's tool table?", labels, 0, False)
        if not ok:
            return
        machine = machines[labels.index(mchoice)]
        mid = machine["internal"]["id"]
        self.act(lambda: self.client.link_set_machine(sid, mid),
                 success="Linked — coverage is now checked against that machine.")

    def selected_record(self):
        row = self._selected_row()
        return row.get("record") if row else None


# ---------------------------------------------------------------------------
# Machines tab (machines -> tool table slots)
# ---------------------------------------------------------------------------

class MachinesTab(_Tab):
    TITLE = "Machines"

    def __init__(self, window, client):
        super().__init__(window, client)
        layout = QtGui.QVBoxLayout(self)
        self.tree = QtGui.QTreeWidget()
        self.tree.setHeaderLabels(["Machine / slot", "Detail", "Diameter",
                                   "Binding"])
        self.tree.setColumnWidth(0, 240)
        self.tree.setColumnWidth(1, 180)
        self.tree.itemSelectionChanged.connect(self._selection_changed)
        self.tree.itemDoubleClicked.connect(lambda *a: self.window.inspect_selected())
        layout.addWidget(self.tree)
        row = QtGui.QHBoxLayout()
        self.bind = QtGui.QPushButton("Bind existing…")
        self.bind.clicked.connect(self._bind)
        row.addWidget(self.bind)
        self.adopt = QtGui.QPushButton("Adopt")
        self.adopt.clicked.connect(self._adopt)
        row.addWidget(self.adopt)
        self.unbind = QtGui.QPushButton("Unbind")
        self.unbind.clicked.connect(self._unbind)
        row.addWidget(self.unbind)
        self.del_slot = QtGui.QPushButton("Delete slot")
        self.del_slot.clicked.connect(self._delete_slot)
        row.addWidget(self.del_slot)
        self.del_machine = QtGui.QPushButton("Delete machine")
        self.del_machine.clicked.connect(self._delete_machine)
        row.addWidget(self.del_machine)
        row.addStretch()
        refresh = QtGui.QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        row.addWidget(refresh)
        layout.addLayout(row)
        self._selection_changed()

    def refresh(self):
        self.tree.clear()
        try:
            machines = self.client.list_machines()
            entries = self.client.list_entries()
        except SmoothError as e:
            self.window.refresh_api_log()
            self._notify("✗ %s" % e)
            return
        self.window.refresh_api_log()
        # name lookup for bound instances (best effort)
        names = {}
        try:
            for inst in self.client.list_instances():
                names[(inst.get("internal") or {}).get("id")] = record_name(inst)
        except SmoothError:
            pass
        self.window.refresh_api_log()
        slots_by_machine = {}
        for entry in entries:
            mid = (entry.get("internal") or {}).get("machine_id")
            slots_by_machine.setdefault(mid, []).append(entry)
        if not machines:
            QtGui.QTreeWidgetItem(self.tree, ["(no machines on the server yet)"])
            return
        for machine in sorted(machines, key=record_name):
            mid = (machine.get("internal") or {}).get("id")
            ctype = field_value(_canonical(machine, "controller_type")) or "—"
            node = QtGui.QTreeWidgetItem(
                self.tree, ["🛠 %s" % record_name(machine), ctype, "", ""])
            node.setData(0, QtCore.Qt.UserRole, ("machine", machine))
            for entry in sorted(
                    slots_by_machine.get(mid, []),
                    key=lambda e: field_value(_canonical(e, "tool_number")) or 0):
                tnum = field_value(_canonical(entry, "tool_number"))
                desc = field_value(_canonical(entry, "description")) or "—"
                dia = field_value(_canonical(entry, "offsets", "diameter"))
                bound = field_value(_canonical(entry, "bound_instance_id"))
                binding = ("✓ %s" % names.get(bound, short_id({"internal": {"id": bound}}))
                           if bound else "unbound")
                child = QtGui.QTreeWidgetItem(
                    node, ["T%s" % tnum if tnum is not None else "slot",
                           desc, _fmt_dia(dia), binding])
                child.setData(0, QtCore.Qt.UserRole, ("slot", entry))
            node.setExpanded(True)
        self._selection_changed()

    def _selected(self):
        rows = self.tree.selectedItems()
        if not rows:
            return None, None
        data = rows[0].data(0, QtCore.Qt.UserRole)
        if not data:
            return None, None
        return data            # (kind, record)

    def selected_record(self):
        _kind, record = self._selected()
        return record

    def _slot_bound(self, entry):
        return field_value(_canonical(entry, "bound_instance_id"))

    def _bind(self):
        kind, entry = self._selected()
        if kind != "slot" or self._slot_bound(entry):
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
            self, "Bind existing tool", "Install which tool in this slot?",
            labels, 0, False)
        if not ok:
            return
        instance = instances[labels.index(choice)]
        iid = instance["internal"]["id"]
        eid = entry["internal"]["id"]

        def on_conflict(err):
            if QtGui.QMessageBox.question(
                    self, "Already installed",
                    "%s\n\nMove it here?" % err,
                    QtGui.QMessageBox.Yes | QtGui.QMessageBox.No
            ) == QtGui.QMessageBox.Yes:
                self.act(lambda: self.client.bind_entry(eid, iid, move=True),
                         success="Moved the tool into this slot.")

        self.act(lambda: self.client.bind_entry(eid, iid),
                 success="Bound the tool into the slot.", on_conflict=on_conflict)

    def _adopt(self):
        kind, entry = self._selected()
        if kind != "slot" or self._slot_bound(entry):
            return
        eid = entry["internal"]["id"]
        name, ok = QtGui.QInputDialog.getText(
            self, "Adopt slot",
            "Name for the new tool (blank = use the slot's description):")
        if not ok:
            return
        self.act(lambda: self.client.adopt_entry(eid, name=name or None),
                 success="Created a new tool from the slot and installed it.")

    def _unbind(self):
        kind, entry = self._selected()
        if kind != "slot" or not self._slot_bound(entry):
            return
        eid = entry["internal"]["id"]
        self.act(lambda: self.client.unbind_entry(eid),
                 success="Unbound (the tool itself is kept).")

    def _delete_slot(self):
        kind, entry = self._selected()
        if kind != "slot":
            return
        eid = entry["internal"]["id"]
        self.act(lambda: self.client.delete_entry(eid),
                 confirm=("Delete slot",
                          "Remove this slot? If the controller re-pushes, it "
                          "returns."),
                 success="Slot removed.")

    def _delete_machine(self):
        kind, machine = self._selected()
        if kind != "machine":
            return
        mid = machine["internal"]["id"]
        result = self.act(
            lambda: self.client.delete_machine(mid),
            confirm=("Delete machine",
                     "Delete '%s' and its tool-table slots? Tools are kept."
                     % record_name(machine)),
            success="Machine deleted.")
        if result and result.get("entries_removed") is not None:
            self._notify("Machine deleted (%d entr%s removed)."
                         % (result["entries_removed"],
                            "y" if result["entries_removed"] == 1 else "ies"))

    def _selection_changed(self):
        kind, record = self._selected()
        is_slot = kind == "slot"
        bound = is_slot and bool(self._slot_bound(record))
        self.bind.setEnabled(is_slot and not bound)
        self.adopt.setEnabled(is_slot and not bound)
        self.unbind.setEnabled(is_slot and bound)
        self.del_slot.setEnabled(is_slot)
        self.del_machine.setEnabled(kind == "machine")


# ---------------------------------------------------------------------------
# Audit tab (read-only)
# ---------------------------------------------------------------------------

class AuditTab(_ListTab):
    TITLE = "Audit"
    HEADERS = ["Time", "Operation", "Entity", "Entity id", "Result"]
    COLUMN_WIDTHS = {0: 200, 1: 110, 2: 170, 3: 100}

    def populate(self):
        logs = self.client.list_audit(limit=50)
        if not logs:
            self._add(["(no audit entries)", "", "", "", ""])
            return
        for entry in logs:
            eid = entry.get("entity_id") or ""
            self._add([
                entry.get("timestamp") or "—", entry.get("operation") or "—",
                entry.get("entity_type") or "—", eid[:8] if eid else "—",
                entry.get("result") or "—",
            ], record=entry)
