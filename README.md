# Photos Local Keyword Indexer — public beta downloads

This repository contains download assets only. The application source and its
private local run data are not published here.

## Current prerelease

Download
[`v0.1.0-beta.1`](https://github.com/kerife/photos-local-keyword-indexer-releases/releases/tag/v0.1.0-beta.1),
including the Apple Silicon DMG and its SHA-256 sidecar.

This is an **unsigned, non-notarized development beta** for macOS 14 or later.
Verify the checksum before opening it. Do not disable Gatekeeper, SIP, or AMFI;
follow the narrow Control-click **Open** / System Settings **Open Anyway** path
documented in the release notes.

Ollama is installed separately. The required model is installed manually with:

```bash
ollama pull qwen3-vl:4b
```

Images are analyzed locally through Ollama. Review every proposed keyword and
caption against the local thumbnail before explicitly confirming Apply.
