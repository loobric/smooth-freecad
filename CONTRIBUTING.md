# Contributing to loobric-freecad

Thanks for your interest in contributing. This is a **reference client** for
[Loobric Server](https://github.com/loobric/loobric-server), licensed **MIT**.

## No CLA — just a DCO sign-off

Unlike the AGPL-licensed `loobric-server` server (which requires a Contributor
License Agreement), the MIT-licensed client repositories do **not** require a
CLA. Instead, we use the **Developer Certificate of Origin (DCO)**: a simple,
per-commit statement that you wrote the patch or otherwise have the right to
contribute it under the project's license.

You agree to the DCO (<https://developercertificate.org/>) by adding a
`Signed-off-by` line to each commit:

```
git commit -s -m "Your message"
```

This appends a trailer using your configured `git` name and email:

```
Signed-off-by: Jane Developer <jane@example.com>
```

Use your real name and a reachable email. CI checks every commit in a pull
request for this trailer and will fail the PR if any commit is missing it. To
fix an existing branch:

```
git rebase --signoff main
```

## Development

- [DEVELOPMENT.md](./DEVELOPMENT.md) — environment setup, repo layout, and tests.
- [TECHNICAL.md](./TECHNICAL.md) — data model and how the FreeCAD formats map to
  the Loobric schema.

## Pull requests

- Reference the issue your change addresses.
- Keep changes focused and the test suite green.
- Be respectful in discussion.
