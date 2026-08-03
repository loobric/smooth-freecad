# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
Loobric FreeCAD Addon - Command definitions.

Defines the Loobric sync command that gets added to the CAM workbench.
"""

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtGui, QtCore
import os


class LoobricSyncCommand:
    """Command to sync tools with Loobric server.
    
    This command opens the sync dialog which allows:
    - Bidirectional sync (import/export)
    - Export only
    - Import only
    """
    
    def GetResources(self):
        # Try to load icon, fallback to default if not found
        icon_path = os.path.join(os.path.dirname(__file__), 'Resources', 'icons', 'Loobric.svg')
        
        return {
            'Pixmap': icon_path if os.path.exists(icon_path) else '',
            'MenuText': 'Loobric',
            'ToolTip': 'Open the Loobric window: sync tools, process the binding '
                       'inbox, and browse tools / sets / machines',
        }
    
    def Activated(self):
        """Execute when command is activated. The window is opened MODELESS so
        FreeCAD (and the CAM tool editors) stay usable beside it; a reference is
        kept on the command so the window isn't garbage-collected."""
        try:
            try:
                from freecad.Loobric import LoobricDialog
            except ImportError:
                import LoobricDialog  # flat layout on sys.path
            window = LoobricDialog.LoobricWindow()
            LoobricSyncCommand._window = window   # keep alive (modeless)
            window.setModal(False)
            window.show()
            window.raise_()
            window.activateWindow()
        except Exception as e:
            App.Console.PrintError(f"Failed to open Loobric window: {e}\n")
            # Show error to user
            from PySide import QtGui
            QtGui.QMessageBox.critical(
                None,
                "Loobric Error",
                f"Failed to open the Loobric window:\n\n{str(e)}"
            )
    
    def IsActive(self):
        """Return True if command should be active.
        
        The command is always available, but the dialog will check
        for proper configuration.
        """
        return True


class LoobricConfigureCommand:
    """Command to configure Loobric connection settings."""
    
    def GetResources(self):
        return {
            'Pixmap': '',
            'MenuText': 'Configure Loobric',
            'ToolTip': 'Configure Loobric server URL and API key'
        }
    
    def Activated(self):
        """Open the Loobric settings, which live on the CAM preference page
        (registered by init_gui via LoobricPreferences). There is no separate
        config dialog — the preference page is the single configuration UI."""
        try:
            try:
                Gui.showPreferences("CAM")
            except TypeError:
                # Older FreeCAD: showPreferences takes no group argument.
                Gui.showPreferences()
        except Exception as e:
            App.Console.PrintError(f"Failed to open Loobric settings: {e}\n")
            QtGui.QMessageBox.information(
                None, "Configure Loobric",
                "Open Edit → Preferences → CAM → Loobric to "
                "configure the server URL and API key.")
    
    def IsActive(self):
        """Return True if command should be active."""
        return True


class JobSetDialog(QtGui.QDialog):
    """The one confirmation for 'Create tool set from job': the (editable) set
    name, every member claim, drift warnings, exclusions, uploads, and — on a
    re-run — the wholesale-replace delta. Nothing reaches the server until OK."""

    def __init__(self, default_name, plan, delta=None, updating=False,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create tool set from job")
        self.resize(520, 420)
        layout = QtGui.QVBoxLayout(self)

        form = QtGui.QHBoxLayout()
        form.addWidget(QtGui.QLabel("Set name:"))
        self.name_edit = QtGui.QLineEdit(default_name)
        form.addWidget(self.name_edit, stretch=1)
        layout.addLayout(form)

        view = QtGui.QPlainTextEdit()
        view.setReadOnly(True)
        lines = ["Members (tool numbers become the set's claims):"]
        for m in plan["members"]:
            flags = []
            if m["needs_upload"]:
                flags.append("will upload")
            if m["drifted"]:
                flags.append("⚠ the job's copy differs from the library tool")
            lines.append("  T%-4s %s%s" % (m["number"], m["name"],
                                           "   (%s)" % ", ".join(flags)
                                           if flags else ""))
        if not plan["members"]:
            lines.append("  (none)")
        if plan["excluded"]:
            lines.append("")
            lines.append("Not included:")
            for e in plan["excluded"]:
                lines.append("  ✖ %s — %s" % (e["name"], e["reason"]))
        if delta:
            lines.append("")
            lines.append("Updating the existing set (members are replaced "
                         "to match the job):")
            lines.extend("  %s" % line for line in delta)
        view.setPlainText("\n".join(lines))
        layout.addWidget(view, stretch=1)

        note = QtGui.QLabel(
            "No FreeCAD tool library file is written — the set lives on the "
            "server and shows read-only in the Sync tab.")
        note.setWordWrap(True)
        layout.addWidget(note)

        buttons = QtGui.QHBoxLayout()
        buttons.addStretch()
        ok = QtGui.QPushButton("Update tool set" if updating
                               else "Create tool set")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        buttons.addWidget(ok)
        cancel = QtGui.QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)

    def chosen_name(self):
        return self.name_edit.text().strip()


class LoobricJobSetCommand:
    """Create or update a Loobric ToolSet from the selected CAM Job's tools.

    The T-numbers of the job's ToolControllers become the set's member claims —
    the numbers posted G-code will call. Deliberately writes NO .fctl: the
    user's tool libraries are their own artifact (jobset.py has the rules).
    Identity for re-runs is the Job's LoobricSetId property (addon-added,
    travels in the .FCStd)."""

    def GetResources(self):
        icon_path = os.path.join(os.path.dirname(__file__), 'Resources',
                                 'icons', 'Loobric.svg')
        return {
            'Pixmap': icon_path if os.path.exists(icon_path) else '',
            'MenuText': 'Create tool set from job',
            'ToolTip': "Create or update a Loobric tool set from this job's "
                       "tools — each tool number becomes the set's claim. No "
                       "FreeCAD tool library file is written.",
        }

    def IsActive(self):
        return bool(self._jobs())

    @staticmethod
    def _jobs():
        try:
            import Path.Main.Job as PathJob
            return PathJob.Instances()
        except Exception:
            return []

    def _pick_job(self):
        """The selected Job, the only one, or a picker over several."""
        jobs = self._jobs()
        selected = [o for o in Gui.Selection.getSelection() if o in jobs]
        if len(selected) == 1:
            return selected[0]
        if len(jobs) == 1:
            return jobs[0]
        if not jobs:
            QtGui.QMessageBox.information(
                None, "Loobric", "The active document has no CAM job.")
            return None
        labels = [j.Label for j in jobs]
        choice, ok = QtGui.QInputDialog.getItem(
            None, "Create tool set from job",
            "Which job?", labels, 0, False)
        return jobs[labels.index(choice)] if ok else None

    @staticmethod
    def _extract(job):
        """ToolControllers -> the plain dicts jobset.plan_job_set consumes."""
        controllers = []
        for tc in (job.Tools.Group if getattr(job, "Tools", None) else []):
            bit = getattr(tc, "Tool", None)
            if bit is None:
                continue
            embedded = None
            try:
                embedded = bit.Proxy.to_dict()
            except Exception:
                pass                       # drift check degrades, nothing else
            controllers.append({
                "name": bit.Label,
                "number": int(tc.ToolNumber),
                "bit_id": getattr(bit, "ToolBitID", None) or None,
                "embedded": embedded,
            })
        return controllers

    def Activated(self):
        try:
            from freecad.Loobric import LoobricDialog, jobset
            from freecad.Loobric.client import LoobricApi, LoobricError
        except ImportError:
            import LoobricDialog, jobset
            from client import LoobricApi, LoobricError

        job = self._pick_job()
        if job is None:
            return
        controllers = self._extract(job)
        if not controllers:
            QtGui.QMessageBox.information(
                None, "Loobric", "Job '%s' has no tool controllers — nothing "
                "to build a tool set from." % job.Label)
            return

        config = LoobricDialog.LoobricConfig.load()
        client = LoobricApi(config["api_url"], config.get("api_key", ""))
        tools_dir = str(LoobricDialog.get_tools_dir())
        log = lambda m: App.Console.PrintMessage("Loobric: %s\n" % m)

        try:
            server_records = client.list_instances()
        except LoobricError as e:
            QtGui.QMessageBox.warning(None, "Loobric",
                                      "Cannot reach the server: %s" % e)
            return
        try:
            plan = jobset.plan_job_set(controllers, tools_dir, server_records)
        except jobset.JobSetError as e:
            QtGui.QMessageBox.critical(None, "Loobric", str(e))
            return
        if not plan["members"]:
            QtGui.QMessageBox.warning(
                None, "Loobric",
                "None of the job's tools could be included:\n\n%s" % "\n".join(
                    "✖ %s — %s" % (e["name"], e["reason"])
                    for e in plan["excluded"]))
            return

        set_id = getattr(job, "LoobricSetId", "") or None
        delta = None
        if set_id:
            try:
                delta = jobset.member_delta(client.get_set(set_id),
                                            plan["members"])
            except LoobricError:
                set_id = None              # stale property: create-or-adopt

        doc_label = job.Document.Label
        dialog = JobSetDialog("%s — %s" % (doc_label, job.Label), plan,
                              delta=delta, updating=set_id is not None,
                              parent=Gui.getMainWindow())
        if dialog.exec_() != QtGui.QDialog.Accepted:
            return
        name = dialog.chosen_name() or "%s — %s" % (doc_label, job.Label)

        try:
            result = jobset.apply_job_set(
                client, tools_dir, plan, name,
                {"document": doc_label, "job": job.Label},
                set_id=set_id, log=log)
        except (LoobricError, OSError) as e:
            QtGui.QMessageBox.warning(None, "Loobric",
                                      "Creating the tool set failed: %s" % e)
            return
        rid = result["set_id"]

        # The name-collision loop: the set exists and is correct; only the
        # name is still unclaimed. Reopen the name until it lands or the user
        # gives up (the set then keeps its previous/derived name).
        while result["name_conflict"]:
            name, ok = QtGui.QInputDialog.getText(
                None, "Name already in use",
                "Another tool set is already named '%s'.\nChoose a different "
                "name:" % name, text=name)
            if not ok or not name.strip():
                App.Console.PrintMessage(
                    "Loobric: name not changed — the set keeps its previous "
                    "name.\n")
                break
            try:
                result["name_conflict"] = not jobset.claim_set_name(
                    client, rid, name.strip())
            except LoobricError as e:
                QtGui.QMessageBox.warning(None, "Loobric", str(e))
                break

        if not hasattr(job, "LoobricSetId"):
            job.addProperty(
                "App::PropertyString", "LoobricSetId", "Loobric",
                "Id of the Loobric tool set created from this job")
            job.setEditorMode("LoobricSetId", 1)     # read-only in the editor
        job.LoobricSetId = rid

        QtGui.QMessageBox.information(
            None, "Loobric",
            "%s tool set '%s' with %d member claim(s).%s" % (
                "Created" if result["created"] else "Updated", name,
                len(plan["members"]),
                "\n\n%d tool(s) were not included — see the report view."
                % len(plan["excluded"]) if plan["excluded"] else ""))
        for e in plan["excluded"]:
            log("not included: %s — %s" % (e["name"], e["reason"]))


class LoobricAssetStoreCommand:
    """EXPERIMENTAL toggle: serve FreeCAD's tool library from Loobric.

    Swaps the CAM asset system's "local" store for a Loobric-backed one
    (assetstore.py): the native tool browsers and editors then show and edit
    server-backed tools, synced invisibly in the background through a local
    mirror (reads work offline). Toggling off restores FreeCAD's own store
    for this session. To make store mode start with the CAM workbench,
    enable it in Edit → Preferences → CAM → Loobric."""

    def GetResources(self):
        icon_path = os.path.join(os.path.dirname(__file__), 'Resources',
                                 'icons', 'Loobric2.svg')
        return {
            'Pixmap': icon_path if os.path.exists(icon_path) else '',
            'MenuText': 'Loobric asset store (experimental)',
            'ToolTip': "Toggle serving FreeCAD's tool library from the "
                       "Loobric server (via a background-synced local "
                       "mirror). Experimental; requires FreeCAD 1.1.",
        }

    def IsActive(self):
        return True

    def Activated(self):
        try:
            from freecad.Loobric import assetstore, storemode
        except ImportError as e:
            try:
                import assetstore, storemode
            except ImportError:
                QtGui.QMessageBox.warning(
                    None, "Loobric",
                    "The Loobric asset store needs FreeCAD 1.1's CAM asset "
                    "system, which this FreeCAD does not provide.\n\n(%s)" % e)
                return

        if assetstore.is_active():
            storemode.deactivate_store()
            QtGui.QMessageBox.information(
                None, "Loobric",
                "Loobric asset store deactivated.\n\nFreeCAD's own local "
                "tool directory is serving the tool library again.")
            return

        # Activation runs in the background (the initial fill can be a full
        # library download); storemode shows the summary dialog when it
        # completes. The status widget and toolbar badge cover the meantime.
        storemode.activate_store(interactive=True)


# Register commands
Gui.addCommand('Loobric_Sync', LoobricSyncCommand())
Gui.addCommand('Loobric_Configure', LoobricConfigureCommand())
Gui.addCommand('Loobric_SetFromJob', LoobricJobSetCommand())
Gui.addCommand('Loobric_AssetStore', LoobricAssetStoreCommand())

App.Console.PrintMessage("Loobric commands registered\n")
