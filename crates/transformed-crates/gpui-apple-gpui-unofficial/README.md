<!-- gpui-archipelago:generated -->
# gpui-apple-gpui-unofficial

_Zed's `gpui_apple` crate, republished for standalone use._

Part of **[gpui-archipelago](https://gpui-archipelago.github.io/)** (aka **GPUI-lago** — "jee pyu ay la go"):
unofficial, automated builds of [Zed](https://github.com/zed-industries/zed)'s
GPUI crates, published to crates.io.

This package is the published build of `gpui_apple`, generated from zed
`v1.25.3` (`8dd255c3eafb5231056fe4e6f601cedd011f1c93`) at version `1.25.3`.

- **Website and project** — <https://gpui-archipelago.github.io/>
- **Source and release automation** — <https://github.com/gpui-archipelago/gpui-unofficial>
- **Original crate** — <https://github.com/zed-industries/zed/tree/v1.25.3/crates/gpui_apple>

## Install

```sh
cargo add gpui-apple-gpui-unofficial
```

A platform backend, selected through `gpui_platform`.

## Usage

Most applications depend on the facade:

```toml
[dependencies]
gpui-unofficial = "1.25.3"
```

Use `gpui-apple-gpui-unofficial` directly when you need this crate on its own:

```toml
[dependencies]
gpui-apple-gpui-unofficial = "1.25.3"
```

## Versioning

Versions mirror Zed's release tags: zed `v1.25.3` publishes as `1.25.3`.
Because the version is taken verbatim from Zed's semver, an already-published
version cannot be replaced; a fix has to wait for the next Zed release.

## Release notes and API changes

- **Release notes** — <https://github.com/gpui-archipelago/gpui-release-notes/issues/3>
- **GPUI fork map** — <https://gpui-archipelago.github.io/map/>
- **API surface changes for `gpui-unofficial`** — <https://gpui-archipelago.github.io/map/#/changes?p=gpui-unofficial>
- **What this project changes on top of Zed** — <https://github.com/gpui-archipelago/gpui-unofficial/blob/main/docs/upstream-patches.md>

## The archipelago

Every fork is an island. There's no mainland and no one's building one, so the
islands have to find each other. Here they can.

Ideally, Zed would have published GPUI as a stable, versioned crate, but that
didn't happen. Various people forked it instead, and each fork ended up solving
a different part of the problem:

- **gpui-unofficial** — publishes up-to-date crates on crates.io, which covers
  distribution. Currently needs a maintainer.
- **gpui-kit** — Longbridge's shadow distribution: republishes upstream Zed
  crates to crates.io as `gpui-pre` under its own namespace, fronted by a
  `gpui-kit` facade over `gpui-base`, `gpui-component`, and `gpui-shell` —
  Jason Lee's answer to 315 days without an official release.
- **gpui-ce** — a community fork working to accelerate testing of upstream
  features and maintain a faster-moving community version, publishing crates
  for standalone desktop apps.
- **Kael** — forked from GPUI direct; selectively tracks upstream while building
  out general-purpose features (gradients, webviews, form controls, component
  library).
- **WGPUI** — GPUI ported to a single cross-platform backend (wgpu + winit)
  instead of per-platform code — leaner, and features land across Windows,
  macOS, Linux, and WebAssembly in one code path. `WgpuSurface` lets you embed
  raw wgpu render passes (3D, game engines, custom shaders, anything) directly
  in your UI tree, making WGPUI suitable as a shell for 3D applications and
  mixed-mode interfaces.

## Provenance and licence

Derived from Zed's GPUI and published as `gpui-apple-gpui-unofficial` under `Apache-2.0`.
This is an unofficial project, not affiliated with or maintained by Zed
Industries. For issues with the upstream crate, see the
[Zed repository](https://github.com/zed-industries/zed).

    source:          https://github.com/gpui-archipelago/gpui-unofficial
    upstream:        https://github.com/zed-industries/zed
    upstream tag:    v1.25.3
    upstream commit: 8dd255c3eafb5231056fe4e6f601cedd011f1c93
    crate:           gpui-apple-gpui-unofficial
    original:        gpui_apple
    version:         1.25.3
