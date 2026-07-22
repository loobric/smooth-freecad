# Loobric for FreeCAD — Development

Contributor notes for **loobric-freecad**, the FreeCAD CAM client for Loobric tool
synchronization. For the data model and FreeCAD-to-schema mapping, see
[TECHNICAL.md](./TECHNICAL.md).

## AI prompt

AI agents working on **any** Loobric repository should incorporate the following
into their responses:

1. Favor a functional style of programming over an object-oriented style.
2. Docstrings are included for every function, class, and module, and document
   the code's assumptions. If those assumptions change, the docstring MUST be
   updated. Do NOT change a docstring without confirming the change is
   intentional with the user.
3. Unit testing is required for all code. Minimize mocks and stubs; where they
   are unavoidable, document the assumption in the docstring.
4. Unit tests focus on the code's assumptions. If those assumptions change, the
   tests MUST be updated. Do NOT change tests without confirming with the user.
5. Changes are incremental and minimal. Avoid large refactors unless asked.
6. Favor TDD: write the test first and confirm it is complete before
   implementing.
7. Keep README, TECHNICAL, and DEVELOPMENT up to date.
8. Reread this prompt and the design philosophy regularly.

## Architecture

The addon is split so that everything except the Qt widgets runs without
FreeCAD. The pure modules (`mapping.py`, `sync.py`, `client.py`, `loobric.py`,
`viewmodel.py`) carry the logic and are tested headless; the GUI files
(`LoobricDialog.py`, `LoobricTabs.py`, `LoobricCommands.py`, `LoobricPreferences.py`,
`init_gui.py`) only wire them into FreeCAD.

See the module table in [TECHNICAL.md](./TECHNICAL.md#module-layout) for the role
of each file. Two points worth repeating here:

- The client is `LoobricApi`, a thin subclass of `loobric.Client`. The vendored
  `loobric.py` is the single place HTTP transport and the API surface live.
  There is no hand-rolled HTTP client and no `requests` dependency.
- The UI is one modeless window with three tabs (Sync, Machines, Audit log) over
  a single shared client. Each tab renders the pure view-model and fires actions
  back through the client; it holds no sync logic of its own.

## Project structure

```
loobric-freecad/
├── freecad/Loobric/
│   ├── init_gui.py          # addon entry point (commands, prefs, toolbar)
│   ├── LoobricCommands.py     # CAM command registration
│   ├── LoobricDialog.py       # the modeless three-tab window shell
│   ├── LoobricTabs.py         # Sync / Machines / Audit tab widgets
│   ├── LoobricPreferences.py  # CAM preference page (+ .ui)
│   ├── viewmodel.py          # pure view-model
│   ├── sync.py               # plan/apply engine (headless)
│   ├── mapping.py            # FreeCAD <-> sectioned schema (headless)
│   ├── client.py             # LoobricApi(loobric.Client)
│   ├── loobric.py            # vendored reference Python client (stdlib only)
│   └── Resources/icons/
├── tests/                    # pytest suite (runs headless)
│   ├── conftest.py           # FakeServer + tools_dir fixtures
│   └── fixtures/             # sample .fctb / .fctl / .fcstd
├── sample_tools/             # example tool files
├── package.xml               # addon metadata
├── pytest.ini
├── README.md
├── TECHNICAL.md
└── DEVELOPMENT.md            # this file
```

## Dependencies

- FreeCAD 1.1 or later with the CAM workbench (runtime).
- Nothing else at runtime: the client is standard-library only (via the vendored
  `loobric.py`).
- For tests: `pytest` (and optionally `pytest-cov`).

## Testing

The suite is headless — it does not require FreeCAD, because the tested modules
import neither FreeCAD nor PySide. It uses an in-memory `FakeServer` fixture
(`tests/conftest.py`) in place of a live server.

```bash
# from the repository root
pytest            # run everything
pytest -v         # verbose
pytest tests/test_plan_apply.py        # one file
```

The suite currently has 81 tests across six files:

| File | Focus |
|---|---|
| `test_mapping.py` | `.fctb` / `.fctl` <-> sectioned schema translation |
| `test_plan_apply.py` | the `sync.py` plan/apply engine |
| `test_sync_cascade.py` | folder-row direction cascade decisions |
| `test_client_sectioned.py` | `LoobricApi` sync-lane calls |
| `test_client_inbox_machines.py` | binding, inbox proposals, machines |
| `test_viewmodel.py` | the view-model builders |

Run the test count yourself rather than trusting this number after changes.

## Testing the addon in FreeCAD

1. Install the addon (clone into `Mod`, or symlink for development).
2. Start FreeCAD and switch to the CAM workbench.
3. Configure the server at **Edit -> Preferences -> CAM -> Loobric** and use
   **Test Connection**.
4. Click the **Loobric** toolbar button and exercise the Sync / Machines / Audit
   tabs.

## Contributing

1. Follow TDD: write the test first.
2. Keep new logic in the headless modules where possible, so it stays testable
   without FreeCAD.
3. Verify round-trip fidelity (FreeCAD -> Loobric -> FreeCAD) for any mapping
   change; unknown keys must survive untouched.
4. Update docstrings when assumptions change.
5. Favor a functional style.

## Troubleshooting

**Addon not appearing in FreeCAD**
- Check it is in the `Mod` directory and that `freecad/Loobric/` and
  `package.xml` are present.
- Restart FreeCAD completely and check the Report view for load errors.

**Loobric button missing from the toolbar**
- Make sure the CAM workbench is active.
- Check the Python console / Report view for errors during initialization.

**Connection errors**
- Confirm the server URL and API key under CAM -> Loobric, and use Test
  Connection.
- Open the window's **Debug -> API log** to see the failing request and status.

**Tool data mismatch**
- Check unit consistency (mm vs inches).
- For a tool changed on both sides, double-click the Sync row to resolve it
  field by field.
