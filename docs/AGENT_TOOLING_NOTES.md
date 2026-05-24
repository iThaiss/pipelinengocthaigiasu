# Agent Tooling Notes

## Use `apply_patch` Correctly

`apply_patch` is a freeform tool. Do not call it with JSON, an empty object, or quoted parameters. The patch body must be sent directly in unified diff format.

Correct tool payload shape:

```diff
*** Begin Patch
*** Add File: example.txt
+hello
*** End Patch
```

Incorrect calls that will abort or do nothing:

```json
{}
```

```json
{"patch":"*** Begin Patch\n..."}
```

## If Direct `apply_patch` Invocation Fails

If the interface makes it difficult to send freeform content directly, use the shell wrapper as a fallback:

```bash
apply_patch <<'PATCH'
*** Begin Patch
*** Add File: example.txt
+hello
*** End Patch
PATCH
```

Keep this fallback limited to patch application. Do not use shell heredocs to write arbitrary files when `apply_patch` can handle the edit.

## Pre-Edit Checklist

- Announce the intended edit before changing files.
- Prefer one coherent patch per logical change.
- Keep patches scoped to the requested work.
- Do not use `{}` or JSON when calling a freeform tool.
- After patching, run the smallest useful validation command.

## Recovery Checklist

If an `apply_patch` call is aborted:

1. Stop retrying the same JSON-shaped call.
2. Re-send the patch as raw freeform diff content.
3. If freeform delivery is unavailable, use `apply_patch <<'PATCH'` through the shell.
4. Verify with `git diff -- <path>` or the relevant test/validation command.
