//! Render the custom per-crate README from `templates/crate-readme.md`.
//!
//! The upstream READMEs assume the Zed monorepo and do not explain the
//! unofficial build. Every transformed crate gets this template instead; the
//! `gpui` README is also included as crate-level rustdoc by upstream
//! (`#![doc = include_str!("../README.md")]`), so it must be self-contained.

use anyhow::{Context, Result};
use std::fs;
use std::path::Path;
use toml_edit::DocumentMut;

use crate::transform::{crate_name_from_path, unofficial_name};

const TEMPLATE: &str = include_str!("../templates/crate-readme.md");

/// Marker written at the top of generated READMEs. `publish` uses it to avoid
/// prepending the old fallback notice to a tree that already has this README.
pub const GENERATED_MARKER: &str = "<!-- gpui-archipelago:generated -->";

const SITE: &str = "https://gpui-archipelago.github.io/";
const REPOSITORY: &str = "https://github.com/gpui-archipelago/gpui-unofficial";
const LICENSE: &str = "Apache-2.0";

/// Write `README.md` into every transformed crate directory.
pub fn write_readmes(
    output_dir: &Path,
    zed_tag: &str,
    zed_commit: &str,
    crates: &[&str],
) -> Result<()> {
    let version = zed_tag.strip_prefix('v').unwrap_or(zed_tag);
    let mut written = 0usize;

    for crate_path in crates {
        let original_name = crate_name_from_path(crate_path);
        let pkg_name = unofficial_name(original_name);
        let crate_dir = output_dir.join(&pkg_name);
        if !crate_dir.is_dir() {
            continue;
        }

        let manifest = fs::read_to_string(crate_dir.join("Cargo.toml")).unwrap_or_default();
        let description = manifest_description(&manifest)
            .filter(|description| !description.starts_with("Unofficial release of Zed's"))
            .unwrap_or_else(|| {
                format!("Zed's `{original_name}` crate, republished for standalone use.")
            });
        let original_path = if crate_path.starts_with("tooling/") {
            crate_path.to_string()
        } else {
            format!("crates/{crate_path}")
        };

        let rendered = render_crate_readme(
            &pkg_name,
            original_name,
            version,
            zed_tag,
            zed_commit,
            &original_path,
            &description,
        );
        fs::write(crate_dir.join("README.md"), rendered)
            .with_context(|| format!("Failed to write README for {pkg_name}"))?;
        written += 1;
    }

    if written > 0 {
        println!("  Wrote {written} crate README(s)");
    }
    Ok(())
}

/// Pure renderer, split out so the template can be tested without a checkout.
pub(crate) fn render_crate_readme(
    pkg_name: &str,
    original_name: &str,
    version: &str,
    zed_tag: &str,
    zed_commit: &str,
    original_path: &str,
    description: &str,
) -> String {
    let usage = usage_for(original_name, pkg_name, version);
    let kind_note = kind_note_for(original_name, pkg_name, version);

    TEMPLATE
        .replace("@CRATE@", pkg_name)
        .replace("@ORIGINAL@", original_name)
        .replace("@DESCRIPTION@", description)
        .replace("@VERSION@", version)
        .replace("@ZED_TAG@", zed_tag)
        .replace("@ZED_COMMIT@", zed_commit)
        .replace("@ORIGINAL_PATH@", original_path)
        .replace("@SITE@", SITE)
        .replace("@REPOSITORY@", REPOSITORY)
        .replace("@LICENSE@", LICENSE)
        .replace("@KIND_NOTE@", &kind_note)
        .replace("@USAGE@", &usage)
}

fn usage_for(original_name: &str, pkg_name: &str, version: &str) -> String {
    let usage = match original_name {
        "gpui" => r#"The package is named `gpui-unofficial`; its library target is `gpui`, so
source keeps `use gpui::*`. Start an application through the platform facade.
The platform package is `gpui-platform-gpui-unofficial`; alias it as
`gpui_platform` so the upstream code shape keeps working:

```toml
[dependencies]
gpui-unofficial = "@VERSION@"
gpui_platform = { package = "gpui-platform-gpui-unofficial", version = "@VERSION@" }
```

```rust,no_run
use gpui::*;

struct Hello;

impl Render for Hello {
    fn render(&mut self, _: &mut Window, _: &mut Context<Self>) -> impl IntoElement {
        div().child("Hello, GPUI!")
    }
}

fn main() {
    gpui_platform::application().run(|cx: &mut App| {
        cx.open_window(WindowOptions::default(), |_, cx| cx.new(|_| Hello))
            .unwrap();
    });
}
```

The host backend is selected through `gpui_platform`; `gpui-unofficial`
already enables the platform features it needs."#
            .to_string(),

        "gpui_platform" => {
            r#"`gpui-unofficial` depends on this crate, but a direct dependency needs a
name for the entry point. Alias it the way the upstream crates do:

```toml
[dependencies]
gpui_platform = { package = "gpui-platform-gpui-unofficial", version = "@VERSION@" }
```

```rust,no_run
use gpui::*;

fn main() {
    gpui_platform::application().run(|cx: &mut App| {
        // open windows here
    });
}
```"#
                .to_string()
        }

        _ => r#"Most applications depend on the facade:

```toml
[dependencies]
gpui-unofficial = "@VERSION@"
```

Use `@CRATE@` directly when you need this crate on its own:

```toml
[dependencies]
@CRATE@ = "@VERSION@"
```"#
            .to_string(),
    };

    usage
        .replace("@VERSION@", version)
        .replace("@CRATE@", pkg_name)
}

fn kind_note_for(original_name: &str, pkg_name: &str, version: &str) -> String {
    let note = match original_name {
        "gpui" => "The main framework crate. It pulls in the platform facade and the host backend.",
        "gpui_platform" => {
            "This is the platform facade (`application()`, backend selection). `gpui-unofficial` already depends on it; add it directly only to use the entry point."
        }
        "gpui_wgpu" => "The wgpu renderer backend, enabled transitively by the platform crates.",
        "gpui_apple" | "gpui_macos" | "gpui_linux" | "gpui_windows" | "gpui_web" => {
            "A platform backend, selected through `gpui_platform`."
        }
        "gpui_macros" => {
            "Proc-macro support for `gpui-unofficial`; normally pulled in transitively."
        }
        "derive_refineable" => "Derive macros for `refineable`; normally pulled in transitively.",
        _ => {
            "A supporting crate in the `gpui-unofficial` stack. Most applications should depend on `gpui-unofficial` instead."
        }
    };

    note.replace("@CRATE@", pkg_name)
        .replace("@VERSION@", version)
}

fn manifest_description(manifest: &str) -> Option<String> {
    let doc: DocumentMut = manifest.parse().ok()?;
    doc.get("package")?
        .as_table_like()?
        .get("description")?
        .as_str()
        .map(str::to_string)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn render(pkg: &str, original: &str) -> String {
        render_crate_readme(
            pkg,
            original,
            "1.25.2",
            "v1.25.2",
            "3b3506ea",
            &format!("crates/{original}"),
            "Test description",
        )
    }

    #[test]
    fn main_crate_readme_has_platform_example() {
        let out = render("gpui-unofficial", "gpui");
        assert!(out.starts_with(GENERATED_MARKER));
        assert!(out.contains("gpui-platform-gpui-unofficial"));
        assert!(out.contains("gpui_platform::application()"));
        assert!(out.contains(SITE));
        assert!(out.contains(REPOSITORY));
        assert!(out.contains("GPUI-lago"));
        assert!(out.contains("https://github.com/gpui-archipelago/gpui-release-notes/issues/3"));
        assert!(out.contains("https://gpui-archipelago.github.io/map/"));
        assert!(out.contains("https://gpui-archipelago.github.io/map/#/changes?p=gpui-unofficial"));
        assert!(out.contains(
            "https://github.com/gpui-archipelago/gpui-unofficial/blob/main/docs/upstream-patches.md"
        ));
        assert!(!out.contains("@CRATE@"));
        assert!(!out.contains("@VERSION@"));
        assert!(!out.contains("@USAGE@"));
    }

    #[test]
    fn supporting_crate_readme_points_at_facade() {
        let out = render("collections-gpui-unofficial", "collections");
        assert!(out.contains("Most applications depend on the facade"));
        assert!(out.contains("collections-gpui-unofficial"));
        assert!(!out.contains("@CRATE@"));
        assert!(!out.contains("@ORIGINAL@"));
    }

    #[test]
    fn no_placeholders_remain_for_any_crate() {
        for (pkg, original) in [
            ("gpui-unofficial", "gpui"),
            ("gpui-platform-gpui-unofficial", "gpui_platform"),
            ("gpui-wgpu-gpui-unofficial", "gpui_wgpu"),
            ("derive-refineable-gpui-unofficial", "derive_refineable"),
        ] {
            let out = render(pkg, original);
            let mut rest = out.as_str();
            while let Some(start) = rest.find('@') {
                let after = &rest[start + 1..];
                let Some(end) = after.find('@') else { break };
                let token = &after[..end];
                assert!(
                    !token.chars().all(|c| c.is_ascii_uppercase() || c == '_'),
                    "unreplaced placeholder @{token}@ in {pkg}"
                );
                rest = &after[end + 1..];
            }
        }
    }

    #[test]
    fn write_readmes_uses_package_description() {
        let dir = tempfile::tempdir().unwrap();
        let crate_dir = dir.path().join("collections-gpui-unofficial");
        fs::create_dir_all(&crate_dir).unwrap();
        fs::write(
            crate_dir.join("Cargo.toml"),
            r#"[package]
name = "collections-gpui-unofficial"
version = "1.25.2"
description = "Shared collections used by GPUI"
"#,
        )
        .unwrap();

        write_readmes(dir.path(), "v1.25.2", "3b3506ea", &["collections"]).unwrap();

        let out = fs::read_to_string(crate_dir.join("README.md")).unwrap();
        assert!(out.contains("Shared collections used by GPUI"));
        assert!(out.contains("collections-gpui-unofficial"));
        assert!(out.contains("v1.25.2"));
        assert!(out.contains("3b3506ea"));
    }
}
