# Photos Local Keyword Indexer — public beta downloads

This repository contains the application source under the MIT license and
public beta downloads. Private local run data, build environments, signing
material and private validation evidence are not published here.

## Current prerelease

Download
[`v0.1.0-beta.1`](https://github.com/kerife/photos-local-keyword-indexer-releases/releases/tag/v0.1.0-beta.1),
including the Apple Silicon DMG and its SHA-256 sidecar.

This is an **unsigned, non-notarized public beta** for macOS 14 or later.
Verify the checksum before opening it. Do not disable Gatekeeper, SIP, or AMFI;
follow the narrow Control-click **Open** / System Settings **Open Anyway** path
documented in the release notes.

Updates are manual: download a newer prerelease and verify its checksum again.
Sparkle is disabled for this unsigned beta. Read the [privacy note](../privacy.md),
[safe-support guide](../support.md), [MIT license](../../LICENSE) and
[acceptance checklist](public-beta-acceptance-checklist.md) before testing.

Ollama is installed separately. The required model is installed manually with:

```bash
ollama pull qwen3-vl:4b
```

Images are analyzed locally through Ollama. Review every proposed keyword and
caption against the local thumbnail before explicitly confirming Apply.
