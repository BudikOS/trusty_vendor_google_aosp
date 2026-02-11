# Trusty VSCode Support

## Quickstart

Once you have VSCode installed, open the Trusty code base in VSCode and create a
`.vscode` folder in the root (it may already exist if you've adjusted any
settings). This folder is needed to tell the install script where the VSCode
workspace begins.

Next run the install script:

```shell
$ ./trusty/vendor/google/aosp/ide/vscode/install.sh
```

This will configure VSCode to understand how to start up Trusty in the emulator
and attach the debugger.

## Building

The configuration files that are provided do not build Trusty for you. You'll
need to do that on the command line:

```shell
$ ./trusty/vendor/google/aosp/scripts/build.py --skip-tests qemu-generic-arm64-test-debug
```

## Enabling Rust Analyzer

Make sure [rust-analyzer VSCode extension][rust-analyzer] is installed and enabled.

Edit `.vscode/settings.json` to include

```json
{
    "rust-analyzer.server.path": "${workspaceFolder}/prebuilts/rust-toolchain/linux-x86/1.92.0/bin/rust-analyzer",
    "rust-analyzer.procMacro.server": "${workspaceFolder}/prebuilts/rust-toolchain/linux-x86/1.92.0/libexec/rust-analyzer-proc-macro-srv",
    "rust-analyzer.linkedProjects": [
        "${workspaceFolder}/build-root/build-qemu-generic-arm64-test-debug/rust-project.json",
        "${workspaceFolder}/build-root/build-qemu-generic-arm64-test-debug/kernellib/rust-project.json"
    ]
}
```

**NOTE**: Verify that the paths are correct as they change frequently. The
`linkedProjects` paths must match the trusty target you built (e.g.
`qemu-generic-arm64-test-debug`,  `vm-arm64-test_os-userdebug`, etc. ) and the
path to rust-analyzer must match `TRUSTY_BUILD_RUST_VERSION` set in
`../scripts/envsetup.sh`.

**NOTE**: Each Trusty build produces separate rust-project.json files for the
kernel and userspace. You can link multiple Trusty builds via `linkedProjects`
as long as all paths remain valid.

This will make sure that the IDE version of rust-analyzer matches the
version that was used to build Trusty Rust code in the previous step.

Open a Rust file (built by Trusty) and verify correct operation by looking for
type annotations or testing that go-to-definition works.

## Debugging

Before starting, go ahead and set your breakpoints by clicking to the left of
the line number in a file. You can do this for kernel code as well as TA code.
Because our integration with LLDB is hacky, the debugger will lose track of a
process after syscalls are made. You'll need to add breakpoints after each
syscall to step past them.

As long as you've built the `qemu-generic-arm64-test-debug` Trusty project
you can switch to the Run/Debug view (View > Run) and click the button that says
`Debug with Prebuilt`.

Now you're debugging like a pro.

[rust-analyzer]:https://marketplace.visualstudio.com/items?itemName=rust-lang.rust-analyzer