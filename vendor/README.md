# vendor/ - optional offline overrides

The build normally downloads a static ffmpeg binary and the model weights. If those hosts
are blocked from your build machine, drop the files here and the build will use them instead
of hitting the network:

| File | What to put here |
|---|---|
| `vendor/ffmpeg` | a static `ffmpeg` executable (e.g. extracted from johnvansickle.com or BtbN builds) |
| `vendor/woof_int8.npz` | the model weights from <https://huggingface.co/ConwayResearch/Underdog-Woof> |

Both are checksum-verified when they come from the network; vendored files are used as-is, so
verify them yourself:

```bash
sha256sum vendor/ffmpeg vendor/woof_int8.npz
```

Expected weights checksum: `d895fbbe84b8f4f461e3e94ab48b1164353b1988ce8d365f5aa8263d0de1203e`.

Nothing in this directory is committed except this README.
