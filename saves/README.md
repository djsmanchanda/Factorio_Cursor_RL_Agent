# Replication save

`mod_playground.zip` is the repository's reproducible Factorio base save.

The native deterministic server manager refreshes this artifact atomically on
every `start`. If `--source-save` is supplied, that source is snapshotted;
otherwise the current isolated server save is used. Verify the artifact before
use with:

```bash
sha256sum saves/mod_playground.zip
```
