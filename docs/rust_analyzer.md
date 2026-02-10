# IDE support for Rust in Trusty

Trusty does not build with Cargo. This means that IDEs typically require some
extra configuration in order to enable navigation, annotation, and refactoring
of Rust code using rust-analyzer.

The Trusty build system will emit two [`rust-project.json`][rust-project] files which describe
the project structure to `rust-analyzer`. One for userspace and one for code in
the kernel.

**NOTE**: We assume Trusty target `qemu-generic-arm64-gicv3-test-debug` was
built with Rust 1.92 like so:

```shell
$ ./trusty/vendor/google/aosp/scripts/build.py --skip-tests qemu-generic-arm64-gicv3-test-debug
```

Please make adjustments to match the actual Rust version and Trusty targets.

## VSCode

See VSCode specific instructions in `../ide/vscode/README.md`.

## Helix

Put the following in `~/.config/helix/languages.toml`

```toml
[[language]]
name = "rust"

[language-server.rust-analyzer]
command = "path/to/prebuilts/rust-toolchain/linux-x86/1.92.0/bin/rust-analyzer"

[language-server.rust-analyzer.config]
linkedProjects = [
  "path/to/build-root/build-qemu-generic-arm64-gicv3-test-debug/rust-project.json",
  "path/to/build-root/build-qemu-generic-arm64-gicv3-test-debug/kernellib/rust-project.json"
]
```

**NOTE:** Be sure to check all paths as they change frequently.


**NOTE:** If you do not set `language-server.rust-analyzer.command` in `languages.toml`, it must be in `$PATH`. Follow [instructions here][rust-analyzer-install]



[rust-project]: https://rust-analyzer.github.io/book/non_cargo_based_projects.html
[rust-analyzer-install]: https://rust-analyzer.github.io/book/rust_analyzer_binary.html#rustup
