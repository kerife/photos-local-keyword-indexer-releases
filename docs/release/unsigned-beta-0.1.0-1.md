# Photos Local Keyword Indexer 0.1.0 (1)

## Unsigned public beta

This prerelease is an Apple Silicon beta for macOS 14 or later. It is not
signed with Apple Developer ID, notarized by Apple, or delivered through the
Mac App Store. macOS may therefore show an unidentified-developer warning on
first launch.

Do not disable Gatekeeper, SIP, or AMFI. After verifying the checksum, mount
the DMG, drag **Photos Local Keyword Indexer** to Applications, Control-click
the app, and choose **Open**. If macOS still blocks it, use the one-app **Open
Anyway** action under System Settings > Privacy & Security.

This is an **unsigned, non-notarized public beta** with manual updates only.
Sparkle is disabled. A SHA-256 match verifies the downloaded file's integrity,
but does not replace an Apple-validated Developer ID identity.

## Included in this beta

- Local visual analysis with Ollama and the open semantic v3 policy.
- Separate review and approval of proposed keywords and short captions.
- Local PhotoKit thumbnails in Review and the final Apply confirmation.
- Optional Apple Maps context for geolocated photos.
- Explicit confirmation before Apply and Rollback.

Images stay on the Mac. Ollama is contacted only through its loopback service.
Thumbnails are requested locally without iCloud download and are not written to
manifests, IPC messages, logs, or a persistent image cache. When Apple Maps is
enabled, only the selected photo's coordinates are sent to Apple for place
context; the coordinates are not stored in the run manifest.

## Requirements

- Apple Silicon Mac.
- macOS 14 or later.
- Ollama installed separately and running locally.
- The model installed manually with:

  ```bash
  ollama pull qwen3-vl:4b
  ```

The app does not download Ollama or models automatically.

## Verify the download

The expected SHA-256 for
`PhotosLocalKeywordIndexer-0.1.0-1-dev-arm64.dmg` is:

```text
012858d8c76995cf2ed2de5e495dfb8c247bb21cab4bbd9afd72ef7df3e1d7d6
```

From Terminal, place the DMG and its `.sha256` file in the same directory and
run:

```bash
shasum -a 256 -c PhotosLocalKeywordIndexer-0.1.0-1-dev-arm64.dmg.sha256
```

Continue only if the result says `OK`.

## First run

1. Start Ollama and confirm `qwen3-vl:4b` is installed.
2. Open the app using the beta trust flow above.
3. Grant Photos access when macOS asks.
4. Start with a one-photo dry-run. A dry-run does not modify Photos.
5. Compare the local thumbnail with each proposed keyword and caption.
6. Approve only the changes you want, then confirm Apply explicitly.

Because this beta uses an ad hoc development seal, a rebuilt copy may be
treated by macOS as a different app and ask for Photos or Automation permission
again.

## Known limitations

- No Developer ID signature or Apple notarization.
- No automatic Sparkle update feed; updates are installed manually from a new
  prerelease.
- Ollama and its models remain separate installations.
- The first beta should be tried with a Photos test library or a small dry-run
  before reviewing a larger batch.

The later paid release track will add Developer ID signing, notarization and a
production update feed. Those properties are intentionally not claimed for
this free beta.

Before testing, read the [privacy note](../privacy.md), [safe-support guide](../support.md),
[MIT license](../../LICENSE), [third-party notices](third-party-notices.md) and
[unsigned-beta acceptance checklist](public-beta-acceptance-checklist.md).
