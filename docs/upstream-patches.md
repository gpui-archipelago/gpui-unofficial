# Upstream patches and workarounds

`gpui-unofficial` is generated from a Zed release, not forked. This file is the
ledger of every place where the generated crates intentionally differ from that
Zed tree, and why.

_Last audited against zed `v1.25.2` (2026-10-22)._

If you add a transform, publish, or packaging workaround, add an entry here in
the same change. A patch without an entry is a maintenance bug: the next person
cannot tell whether it is still needed.

The implementation is authoritative:
- `xtask/src/transform.rs` — source and manifest rewrites
- `xtask/src/publish.rs` — pre-publish rewrites
- `xtask/src/test_isolated.rs` — isolated packaging harness
- `.github/workflows/sync.yml` — platform matrix and flags

Kinds used below:

| kind | meaning |
| --- | --- |
| **source** | rewrites a `.rs` file copied from Zed |
| **manifest** | rewrites a generated `Cargo.toml` |
| **docs** | replaces a crate README/documentation file |
| **publish** | changes the generated tree immediately before `cargo publish` |
| **packaging** | works around how Cargo packages or builds an isolated crate |
| **pipeline** | CI-only flag or job-shape requirement |

## Summary

| id | kind | patch | why | implementation | tests |
| --- | --- | --- | --- | --- | --- |
| SRC-1 | source | Adapt `gpui_wgpu` to crates.io wgpu 30.0.1 | Zed pins upstream wgpu after 30.0.1; crates.io cannot take git deps | `patch_gpui_wgpu_source` | `patch_gpui_wgpu_source_adapts_post_30_0_1_apis` |
| SRC-2 | source | Remove `inspector` cfgs from gpui/gpui_macros | The inspector feature is not published and creates a cycle | `patch_inspector_cfgs` | `patch_inspector_cfgs_handles_test_gated_pattern` |
| SRC-3 | source | Vendor shader-type sources into `gpui_apple` | `build.rs` reaches outside the crate to `../gpui/src` | `patch_gpui_apple_build_rs` | `patches_gpui_apple_build_rs_manifest_path` |
| SRC-4 | source | Drop `unsafe` around `NSBeep()` | `unused_unsafe` becomes an error under `-Dwarnings` | `patch_gpui_macos_source` | (none) |
| SRC-5 | source | Remove external font from `gpui` text example | The example embeds an asset outside the crate | `patch_text_example` | (none) |
| SRC-6 | source | Vendor assets reached by `include_bytes!`/`include_str!` | Zed crates embed repo-root `assets/`; published crates cannot | `vendor_external_assets` | `vendors_assets_referenced_from_outside_the_crate`, `leaves_includes_that_stay_inside_the_crate_alone`, `ignores_includes_it_cannot_resolve`, `builds_relative_include_paths_with_forward_slashes` |
| DOC-1 | docs | Generate a custom per-crate README | Upstream READMEs assume the monorepo and do not explain provenance/install | `readme::write_readmes`, `xtask/templates/crate-readme.md` | `main_crate_readme_has_platform_example`, `supporting_crate_readme_points_at_facade`, `no_placeholders_remain_for_any_crate`, `write_readmes_uses_package_description` |
| MAN-1 | manifest | Rename packages, set version/repository/description/license, set `gpui` lib name | crates.io requirements; keep `use gpui::...` working | `transform_cargo_toml` | many in `transform.rs` |
| MAN-2 | manifest | Alias internal dependencies to `*-gpui-unofficial` | Source keeps original crate names; published packages are renamed | `transform_dependencies` | `local_deps_use_path_and_version_for_internal_siblings` |
| MAN-3 | manifest | Resolve workspace dependencies; strip git fields; union features; honor `default-features` override | crates.io rejects git; Cargo's `workspace = true` semantics are not a plain copy | `resolve_workspace_dep` | `workspace_dep_features_are_unioned_not_replaced`, `workspace_dep_default_features_override_is_honored` |
| MAN-4 | manifest | Substitute or remove git-only dependencies; preserve features/default-features | crates.io rejects git-only deps; zed has several | `transform_dependencies` fallback, `known_git_dep_version`, `lookup_crates_io_version` | `git_only_dep_substitution_preserves_features` |
| MAN-5 | manifest | Clean `[features]` entries that referenced removed optional deps | Dangling `dep:`/`dep?/` references make the manifest unparsable | `remove_dep_from_features` | `removes_dep_prefixed_feature_entries`, `matches_every_activation_form_without_overreaching` |
| MAN-6 | manifest | Remove the `inspector` feature and its deps/forwarding | Unpublished deps and a `gpui_macros` ↔ `gpui` packaging cycle | `remove_inspector_feature` | `remove_inspector_feature_strips_dev_dependency_too`, `inspector_feature_forwarding_removed_from_gpui_platform`, `inspector_dev_dependency_fully_removed_through_full_transform` |
| MAN-7 | manifest | Add `serde/derive` when source derives `Serialize`/`Deserialize` | Standalone crates lose workspace feature unification | `ensure_serde_derive_feature` | `serde_derive_feature_is_added_when_source_uses_it` |
| MAN-8 | manifest | Re-add crates.io `proptest` in the upstream target table and declare its feature | `proptest` is a git-only pin; gpui/sum_tree tests need it; wasm must not get it | `add_proptest_dependency` | four tests listed below |
| MAN-9 | manifest | Declare custom `check-cfg` values; allow deprecated cocoa APIs | `-Dwarnings` turns unknown cfg and deprecation warnings into errors | `add_custom_cfg_lints` | `allows_cargo_clippy_cfg_for_every_objc_crate`, `allows_deprecated_cocoa_apis_in_gpui_apple_and_gpui_macos` |
| MAN-10 | manifest | Add empty `[workspace]`; remove workspace lints | Extracted crates must build standalone | `transform_cargo_toml` | covered indirectly by transform tests |
| MAN-11 | manifest | Only expect crates from the release that introduced them | `gpui_apple` does not exist before zed 1.17 | `CRATE_INTRODUCED_IN`, `crate_exists_at_tag` | `crates_are_only_expected_from_the_release_that_introduced_them`, `unlisted_crates_are_expected_at_every_tag`, `parses_major_minor_from_stable_and_preview_tags` |
| PUB-1 | publish | Strip git fields; replace/remove git-only deps | crates.io rejects any `git` field, even with a version | `patch_git_deps_for_publish`, `patch_dep_section_git`, `known_fork_version` | (none) |
| PUB-2 | publish | Strip internal dev-dependencies | `cargo package` resolves dev-deps; they create cycles/forward refs | `strip_internal_dev_deps_everywhere` | (none) |
| PUB-3 | publish | Fallback unofficial notice for old `crates/` trees | Clarify provenance when the template was not generated | `patch_readme_for_publish` | (none) |
| PUB-4 | publish | Publish in dependency order with delays/retries | crates.io rate limits and sparse-index propagation | `CRATE_PUBLISH_ORDER`, `publish::run` | (none) |
| PKG-1 | packaging | Vendor external deps from a synthetic aggregator | `cargo package` ignores `[patch]`/`[replace]`; needs a directory source | `build_external_vendor_dir` | `collects_plain_and_targeted_external_dev_deps_but_skips_path_deps` |
| PKG-2 | packaging | Use a directory source and seed packaged siblings | Later crates must resolve earlier unpublished siblings | `write_source_replace_config`, `add_to_vendor_dir` | (none) |
| PKG-3 | packaging | Skip compile for crates with replaced git deps, and `util` | Zed forks' APIs differ from crates.io; those crates publish `--no-verify` | `package_and_vendor` | (none) |
| PKG-4 | packaging | Only test crates supported on the current host | macOS/Linux/Windows/web backends cannot all build everywhere | `is_supported_on_current_host` | (none) |
| PKG-5 | packaging | Canonicalize temp paths on macOS | `/var` is a symlink to `/private/var`; build scripts mix `file!()` and `CARGO_MANIFEST_DIR` | `canonicalize_no_verbatim` | (none) |
| CI-1 | pipeline | Build Linux gpui/gpui_linux with `wayland,x11` | The Linux backends are feature-gated | `crate_features` in `scripts/ci-fix`, `sync.yml` | `scripts/ci-fix/verify.sh` |
| CI-2 | pipeline | Build web with `-Zbuild-std` and atomics flags | gpui_web needs atomics and build-std | `sync.yml` | (CI only) |
| CI-3 | pipeline | Run examples and isolated packaging on every supported platform | In-monorepo `cargo check` does not catch package-time failures | `sync.yml` | (CI only) |

---

## Source patches

### SRC-1 — `gpui_wgpu` and wgpu 30.0.1

**Upstream shape.** zed v1.25.2 pins `wgpu = { git = "https://github.com/gfx-rs/wgpu", rev = "3b3506ea...", default-features = false }`.
That commit is *"Fix issue with surface drop panic (#10230)"* (2026-09-12) and
is hundreds of commits past the `v30.0.1` tag. It is upstream wgpu, not a Zed
fork.

**Change.** The transform maps the git-only dependency to crates.io `wgpu
30.0.1` and rewrites the copied `gpui_wgpu` sources:

- drop `default_queue: wgpu::QueueDescriptor::default()` from
  `DeviceDescriptor` literals (`wgpu_context.rs`, `wgpu_atlas.rs`);
- rename `TextureFormat::has_srgb_suffix()` to `is_srgb()`
  (`wgpu_renderer.rs`).

**Why.** `30.0.1` already has `SurfaceColorSpace`,
`SurfaceConfiguration::color_space`, `RequestAdapterOptions::apply_limit_buckets`,
and `Queue::present`. It lacks the three APIs above. `request_device` in 30.0.1
creates the default queue itself, and `is_srgb` is the same predicate as
`has_srgb_suffix` (`*self != self.remove_srgb_suffix()`), so the shim is
behavior-preserving for zed v1.25.2.

**Remove when.** An upstream wgpu release contains the three APIs and zed pins
it. Bump `known_git_dep_version` and delete `patch_gpui_wgpu_source`.

**Caveat.** If zed starts passing a non-default `QueueDescriptor` or relies on
other post-30.0.1 wgpu behavior, this is no longer a rename/removal. Re-evaluate
before extending the shim.

### SRC-2 — inspector cfg rewrites

**Upstream shape.** gpui and gpui_macros gate code with
`#[cfg(any(feature = "inspector", debug_assertions))]` and several variants
(negated, `rust_analyzer`-combined, `all(test, …)`).

**Change.** Rewrite those cfgs to their `debug_assertions` equivalents.

**Why.** The `inspector` feature is removed (MAN-6), so leaving those cfg
references points at an unknown feature and fails `unexpected_cfgs` under
`-Dwarnings`.

**Remove when.** Inspector is published as a separate, cycle-free feature.

### SRC-3 — `gpui_apple` shader types

**Upstream shape.** `gpui_apple/build.rs` runs cbindgen over
`../gpui/src/{scene,geometry,color,window,platform}.rs`.

**Change.** Copy those five files into `gpui/src/` inside the crate, rewrite
`.join("../gpui")`, `.join("../gpui-unofficial")`, and `GPUI_MANIFEST_DIR` to
the crate-local `gpui` directory, and add an `include` list so the copied tree
is packaged.

**Why.** A published crate cannot read a sibling directory. A build-dependency
on gpui would be heavy and reintroduce a cycle.

**Remove when.** gpui_apple owns or vendors the types it needs.

### SRC-4 — `gpui_macos` `NSBeep`

**Upstream shape.** `unsafe { NSBeep() }` where `NSBeep` is safe.

**Change.** Remove the `unsafe` block.

**Why.** The sync build sets `RUSTFLAGS=-Dwarnings`, so `unused_unsafe` becomes
an error. This patch only matters on macOS.

**Remove when.** Upstream removes the unnecessary `unsafe`.

### SRC-5 — text example font

**Upstream shape.** `gpui/examples/text.rs` embeds
`../../../assets/fonts/lilex/Lilex-Regular.ttf` with `include_bytes!`.

**Change.** Delete the font-loading block and the now-unused `Cow` import.

**Why.** The path leaves the crate and breaks when the example is built from a
package. The example is a demo, not library functionality, so dropping the
custom font is the minimal fix.

**Remove when.** Upstream vendors the font into the crate or drops the custom
font from the example.

### SRC-6 — external asset vendoring

**Upstream shape.** `gpui_web` embeds eight bundled fonts from the repo-root
`assets/`; gpui and gpui_wgpu tests/benches use similar includes.

**Change.** Copy each out-of-crate asset into the crate, copy sibling
`LICENSE*`/`COPYING*`/`OFL*` files, and rewrite the include path relative to
the source file.

**Why.** Published crates can only read their own package. The gpui_web fonts
are load-bearing (a browser has no system font fallback); the OFL requires the
license to travel with the font.

**Remove when.** Upstream vendors these assets inside the crates.

---

## Documentation

### DOC-1 — custom per-crate README

**Upstream shape.** Each zed crate ships its own README. Some assume the Zed
monorepo (repo-relative links, workspace instructions, editor-specific
context), and none explain that the crate on crates.io is an automated,
unofficial build.

**Change.** After the transform, `readme::write_readmes` overwrites `README.md`
in every generated crate with `xtask/templates/crate-readme.md`, substituting
the crate name, original crate/path, version, zed tag and commit, description,
and a crate-specific usage/kind note. The template also links the release
notes, the fork map, the measured API-surface changes for `gpui-unofficial`, and
[this ledger](https://github.com/gpui-archipelago/gpui-unofficial/blob/main/docs/upstream-patches.md).
The `gpui` README is also the crate-level rustdoc
(`#![doc = include_str!("../README.md")]`), so the template is written before
any compile.

**Why.** A published crate needs installation and provenance in its own
context. The template also carries the archipelago context and the correct
`gpui_platform` setup, which the upstream README assumes the reader already
knows. Upstream README links that point into the monorepo are replaced rather
than left broken.

**Remove when.** Upstream READMEs are suitable for standalone crates and the
unofficial distribution (not expected).

**Fallback.** `publish::patch_readme_for_publish` remains for `crates/` trees
created before the template existed; it skips any README carrying
`readme::GENERATED_MARKER`.

---

## Manifest transforms

### MAN-1 — package identity

Rename `{crate}` to `{crate}-gpui-unofficial` (with `gpui` → `gpui-unofficial`),
set the version from the zed tag, set the repository, remove `publish = false`,
and ensure `license` and `description` exist. For `gpui`, set `[lib].name =
"gpui"` so consumers keep `use gpui::...`, and add a dev-dependency alias for
`gpui_platform`.

**Why.** crates.io requires description/license/repository; source compatibility
requires the original crate names to keep working through `package` aliases.

### MAN-2 — internal dependency aliases

Rewrite internal `workspace = true` dependencies to:

```toml
name = { package = "name-gpui-unofficial", version = "…" }
```

Preserve features and `optional`, and strip `inspector`. In `--local` mode add
a sibling `path` plus the version (cargo package rejects path-only deps).

**Why.** Published crates reference renamed packages, while source still writes
`use collections::…` etc. Cargo's `package` key provides the alias.

### MAN-3 — workspace dependency resolution

Strip `git`/`rev`/`branch`/`tag` from workspace dependencies, keep
`version`/`package`/`default-features`, union the workspace and usage feature
lists, and let the member's `default-features` override the workspace value.

**Why.** crates.io rejects git fields even when a version is present. Cargo's
`workspace = true` semantics union features and override `default-features`;
copying only the workspace entry silently dropped both. The concrete failures
were `derive_more`'s `deref` features and `chrono`'s `default-features = true`.

### MAN-4 — git-only dependency substitution

For a git-only dependency (no version) in a non-optional `[dependencies]`, map
it to a known crates.io version or the crates.io lookup result. Preserve the
member's features and `default-features`, and the `package` alias. In optional
or dev/build sections, drop it and clean up its `[features]` entries.

Known mappings live in `transform::known_git_dep_version`; the publish-time
duplicate is `publish::known_fork_version`. They must stay in sync.

**Why.** crates.io cannot accept a git dependency. The wgpu case is SRC-1;
`proptest` is MAN-8; other Zed forks are handled at publish time (PUB-1).

### MAN-5 — feature cleanup

When a dependency is removed, delete every `[features]` entry that activates it,
in all Cargo forms: `dep`, `dep:`, `dep/feature`, and `dep?/feature`.

**Why.** A dangling `dep:foo` makes Cargo reject the manifest:
"feature `x` includes `dep:foo`, but `foo` is not listed as a dependency".

### MAN-6 — inspector feature removal

Remove the `inspector` feature from `gpui`, `gpui_macros`, and `gpui_platform`,
remove dependencies whose only feature is `inspector` (including the
`gpui_macros` dev-dependency on `gpui`), and remove
`inspector = ["gpui/inspector"]` forwarding.

**Why.** The inspector pulls in crates outside the publish set, and
`gpui_macros` → `gpui` → `gpui_macros` is a packaging cycle. The removal must
happen before dependency transformation so the `["inspector"]`-only dependency
is still detectable.

### MAN-7 — serde derive

If a crate's source uses `#[derive(Serialize)]` or `#[derive(Deserialize)]`,
ensure its `serde` dependency enables `derive`.

**Why.** zed's workspace relies on feature unification from other members. Once
`http_client` is extracted, the implicit `derive` disappears and
`#[derive(Deserialize)]` fails with "cannot find derive macro".

### MAN-8 — proptest

`proptest` is a git-only dev-dependency in zed. The transform strips it, then
re-adds crates.io `proptest` as an optional dependency and as a dev-dependency
in the same target table upstream used. It also declares an explicit
`proptest = ["dep:proptest"]` feature and enables it from `test-support`.

**Why.** gpui and sum_tree tests use proptest. zed gates it behind
`cfg(not(target_family = "wasm"))` because `rusty-fork`/`wait-timeout` are
process-based and do not build for wasm. Re-adding it untargeted breaks the
wasm example build. The explicit feature keeps `cfg(feature = "proptest")`
known to `unexpected_cfgs`.

### MAN-9 — custom cfg lints and deprecated cocoa

Declare `check-cfg` values for cfgs that Zed's build sets or emits:

| crate | cfg |
| --- | --- |
| `ztracing` | `ztracing`, `ztracing_with_memory` |
| `util_macros` | `perf_enabled` |
| `gpui` | `rust_analyzer` |
| `gpui_apple`, `gpui_macos` | `feature = "cargo-clippy"` |
| `gpui_linux` | `feature = "nightly_coverage"` |

For `gpui_apple`/`gpui_macos`, also set `[lints.rust] deprecated = "allow"`.

**Why.** `-Dwarnings` plus `unexpected_cfgs` makes unknown cfg an error. Those
two crates still use deprecated `cocoa` APIs that upstream has not migrated to
`objc2-foundation`.

### MAN-10 — standalone workspace

Remove `[lints]` (workspace-inherited lints are not valid in a standalone
crate) and add an empty `[workspace]` table so Cargo does not walk up into a
parent workspace.

### MAN-11 — crate introduction tags

`CRATE_INTRODUCED_IN` records the first zed `(major, minor)` for crates that did
not exist across the whole supported tag range (currently `gpui_apple` from
1.17). `verify` filters the expected crate set with it.

**Why.** Otherwise syncing an older tag fails with "Crate not found", and
`verify` waits forever for a crate that release can never contain.

---

## Publish-time patches

### PUB-1 — strip git dependencies

`patch_git_deps_for_publish` runs on the generated tree immediately before
publishing:

- git + version: remove `git`/`rev`/`branch`/`tag`, keep `version`;
- git-only in `[dependencies]`: replace with a crates.io version from
  `lookup_crates_io_version` or `known_fork_version`;
- git-only in optional/dev/build sections: remove it and clean `[features]`.

**Why.** cargo/crates.io rejects any dependency with a `git` field. This is a
second safety net even though the transform already resolves most git deps.

`known_fork_version` covers `zed-font-kit` (0.14), `zed-scap` (0.0.8), and
`proptest` (1); it delegates shared names such as `wgpu` (30.0.1) to
`transform::known_git_dep_version` so the two stages cannot disagree.

### PUB-2 — strip internal dev-dependencies

Remove every `*-gpui-unofficial` dev-dependency, including target-gated tables.

**Why.** `cargo package` resolves dev-dependencies even with `--no-verify`, and
internal dev-deps create cycles (`gpui` dev-depends on `gpui_platform`, which
depends on `gpui`). Consumers never need them.

### PUB-3 — README fallback

If a `crates/` tree predates the README template, prepend the old
unofficial-release notice and rename the first heading. Trees generated by the
current transform already carry `readme::GENERATED_MARKER`, so this is a no-op
for them.

**Why.** Keep provenance clear when publishing from an older local tree or a
partially generated one.

### PUB-4 — publish order and delays

Publish in `CRATE_PUBLISH_ORDER` (topological), with a 90-second propagation
delay, retries/backoff for rate limits, and the 5-new-crates burst / 10-minute
delay rule.

**Why.** crates.io's sparse index takes time to expose a new version, and new
crate creation is rate-limited.

---

## Isolated packaging

### PKG-1 — synthetic aggregator + `cargo vendor`

`test-isolated` creates a temporary aggregator crate that path-depends on every
generated crate with all declared features, plus each crate's external
dev-dependencies, then runs `cargo vendor` once. The vendor directory becomes a
directory source for every `cargo package`/`cargo check`.

**Why.** `cargo package` ignores `[patch]`/`[replace]`, so the only way to make
an isolated package resolve its unpublished siblings is a directory source.
Vendoring from the aggregator captures the full union of external deps,
including optional features and target-gated dev-deps, in one pass.

### PKG-2 — directory source and sibling seeding

Write `.cargo/config.toml` with `[source.crates-io] replace-with =
"vendored-sources"` at the shared crates root and inside each unpacked sandbox.
After a sibling is successfully packaged, copy it into the vendor directory as
`name-version/` with an empty `.cargo-checksum.json`.

**Why.** Later crates in the publish order must resolve earlier, not-yet-published
siblings. The empty checksums skip file verification for packages produced by
our own trusted `cargo package` step.

### PKG-3 — skip compile for fork-dependent crates

In the isolated sandbox, skip `cargo check` when the packaged manifest contains
a replaced git dependency marker or the crate is `util`.

**Why.** Crates that depend on Zed's forks (e.g. `smol`) use APIs that differ
from the crates.io releases. They are intentionally published with
`--no-verify`; packaging still verifies manifest resolution.

### PKG-4 — host-only matrix

`is_supported_on_current_host` limits the isolated test set: macOS crates on
macOS, Linux on Linux, Windows on Windows; web is skipped because it needs
`wasm32-unknown-unknown`.

**Why.** The pipeline runs on a single runner per platform; cross-platform
backend crates cannot compile on the wrong host.

### PKG-5 — path canonicalization

`canonicalize_no_verbatim` resolves symlinks (notably macOS `/var` →
`/private/var`) and strips Windows `\\?\` prefixes.

**Why.** Cargo canonicalizes `CARGO_MANIFEST_DIR`, but build scripts using
`file!()`-derived paths may not. A symlinked vendor/sandbox path makes the two
forms diverge mid-build (e.g. askama template lookups in `mkv-element`).
Windows manifests reject the `//?/` form as an invalid path URL.

---

## CI and platform flags

### CI-1 — Linux features

`gpui` and `gpui_linux` are checked with `--features wayland,x11` on Linux.

**Why.** The Linux backends are feature-gated and are not the default set on
every host.

### CI-2 — web build

The web job uses nightly, `-Zbuild-std=std,panic_abort`, target
`wasm32-unknown-unknown`, and
`RUSTFLAGS="-C target-feature=+atomics,+bulk-memory,+mutable-globals"` with
`RUSTC_BOOTSTRAP=1`.

**Why.** gpui_web needs atomics and build-std; those are not available on the
stable wasm target.

### CI-3 — examples and isolated packaging

Every supported platform builds examples and runs `test-isolated` after
`cargo check`.

**Why.** A crate can compile in the monorepo and still fail `cargo package`
because a path, asset, manifest rewrite, or sibling dependency is missing.

---

## Known gaps and hazards

- **No dedicated tests** for SRC-4, SRC-5, PUB-1..4, PKG-2..5, or CI-2/3.
  They are covered by platform CI or by full release runs, not unit tests.
  Add a fixture-based test when changing one of them.
- **Fork-version mappings are split by stage.** Shared mappings (currently
  wgpu) live in `transform::known_git_dep_version`; publish-only forks live in
  `publish::known_fork_version`, which delegates shared names to the transform.
  Add new shared names to the transform table so both stages agree.
- **Versions mirror Zed tags.** There is no patch-release suffix, so a fix for
  an already-published version cannot be published as `1.25.2` again. This is a
  known README limitation.
- **The wgpu shim is version-specific.** It is safe only while zed's
  `QueueDescriptor` use is the default and `has_srgb_suffix` remains equivalent
  to `is_srgb`. Re-check on every zed bump.
- **`crates/` is generated.** Never edit it directly; every fix belongs in
  `xtask/` or the workflow. `scripts/ci-fix/reproduce.sh` regenerates it.

## Adding a patch

1. Reproduce the failure with `scripts/ci-fix/collect.sh` and
   `scripts/ci-fix/reproduce.sh`.
2. Add the smallest rewrite, guarded on the crate/shape it targets. It must be
   a no-op when the upstream shape is absent.
3. Add a fixture-based unit test next to the existing `transform.rs` tests.
4. Add an entry to the summary table and a detail section here, including the
   **remove when** condition.
5. Run `cargo test -p xtask` and `scripts/ci-fix/verify.sh`.
6. If the patch changes the published contents, call that out in the PR.

Prefer a source/manifest rewrite in `xtask/src/transform.rs` over a one-off
shell patch in CI: the transform runs for every release and is testable.
