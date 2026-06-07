# Security policy

## Reporting a vulnerability

If you find a security issue in brewbridge, please **don't open a public
GitHub issue** — email the maintainer directly at
`hjaltileifsson@gmail.com` with details. Include:

- A short description of the issue.
- The brewbridge version (`brewbridge --version` or check
  `src/brewbridge/__init__.py:__version__`).
- Steps to reproduce if you have them.
- Any proof-of-concept code.

You'll get a reply within a few days. Once the fix is ready it'll go
into a release on a coordinated date so users get an upgrade path.

## Threat model

brewbridge is a desktop tool that:

- Reads and writes a local SQLite database
  (`%APPDATA%\BeerSmith4\BeerSmith.sqlite` on Windows;
  `~/Library/Application Support/BeerSmith4/` on macOS).
- Makes outbound HTTPS requests to `https://www.brew.is/`.
- Registers a `brewis://` URL handler that invokes the
  brewbridge.exe with a URL argument.
- Drives Chromium (via Playwright) to interact with the brew.is
  Recipe Machine form.

What this means in practice for the threat surface:

- **URL handler.** Any process on the same machine can fire a
  `brewis://order/<anything>` URL and have brewbridge process it. The
  handler only does database reads + generating a local HTML file —
  no DB writes, no privileged actions, no shell-out to anything other
  than the user's default browser (to open the resulting HTML) and
  Chromium (when --fill is requested). Worth a look if you spot a way
  for a malicious URL to escalate beyond that.

- **brew.is response handling.** Sync parses the Nuxt payload from
  `/uppskriftir`. A malicious or compromised brew.is response could
  conceivably inject crafted product names that bypass the matcher's
  family checks and cause incorrect (but not unsafe) library writes.
  We don't currently signature-verify the payload — same trust
  assumption as a browser visiting the page.

- **specs_reference.json fallback.** brewbridge ships with a bundled
  snapshot of BeerSmith built-in ingredient specs (~1.3 MB), used to
  re-seed the matcher on a fresh install. The bundled file isn't
  signed; if an attacker can replace the file at install time they
  can influence subsequent matching results (no DB takeover, just
  wrong colour/alpha/attenuation values stamped into library rows).

- **Tray subprocess shell-out.** The recipe picker spawns
  `brewbridge.exe brewis://...` via `subprocess.Popen` with the URL
  passed as a single argv element. The URL comes from a recipe-name
  lookup in the local DB; we don't accept arbitrary user input for it.

If anything in the above looks wrong or you spot something not covered,
please get in touch.

## Supported versions

We only ship security patches for the latest minor release. The
versioning scheme is SemVer-ish but pre-1.0, so any 0.x → 0.(x+1)
release may introduce breaking changes; bugfix-only releases go to
0.x.y patch bumps.
