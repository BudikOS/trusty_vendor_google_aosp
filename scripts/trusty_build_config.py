#!/usr/bin/env python3
#
# Copyright (C) 2018 The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
#
# This test script to be used by the build server.
# It is supposed to be executed from trusty root directory
# and expects the following environment variables:
#
"""Parse trusty build and test configuration files."""

import argparse
import os
import re
import warnings
from enum import StrEnum, auto
from typing import List, Dict, Optional, Self

script_dir = os.path.dirname(os.path.abspath(__file__))

class PortType(StrEnum):
    TEST = auto()
    BENCHMARK = auto()


class TrustyBuildConfigProject(object):
    """Stores build enabled status and test lists for a project

    Attributes:
        build: A boolean indicating if project should be built be default.
        tests: A list of commands to run to test this project.
        also_build: Set of project to also build if building this one.
    """

    def __init__(self):
        """Inits TrustyBuildConfigProject with an empty test list and no
           build."""
        self.build = False
        self.tests = []
        self.also_build = {}
        self.signing_keys = None


class TrustyPortTestFlags(object):
    """ Represents an environment's feature flags.

    There are two use cases:
      * For an environment to specify what features it provides. Setting a flag
        to True means that the environment has the corresponding feature.
        Setting to False or not setting any value for a flag means that the
        corresponding feature is not supported.
      * For a test to specify which features it requires from its environment.
        Setting a flag to True means the test needs the correspoinding feature
        to run, setting to False means the test depends on the _absence_ of that
        feature, and not setting any value means the test does not care about
        that feature and can run whether or not the environment provides it.
    """

    ALLOWED_FLAGS = {"android", "storage_boot", "storage_full",
                     "storage_staging", "storage_tp", "smp4", "abl", "tablet"}

    def __init__(self, **flags: bool) -> None:
        self.flags: dict[str, bool] = {}
        self.set(**flags)

    def set(self, **flags: bool) -> None:
        """Set flags."""
        for name, arg in flags.items():
            if name not in self.ALLOWED_FLAGS:
                raise TypeError(f"Unexpected flag: {name}")

            if self.flags.get(name, arg) != arg:
                warnings.warn(
                    f"Overwritting value for {name}: was {self.flags[name]},"
                    f" now {arg}"
                )

            self.flags[name] = arg

    def set_provide(self, **flags: bool) -> None:
        """Adjust provide flags. flag=True adds a flag, flag=False removes it."""
        for name, arg in flags.items():
            if name not in self.ALLOWED_FLAGS:
                raise TypeError(f"Unexpected flag: {name}")

            if arg:
                self.flags[name] = arg
            else:
                self.flags.pop(name, None)

    def match_provide(self, provide: Self) -> bool:
        for name, arg in self.flags.items():
            if arg != provide.flags.get(name, False):
                # If the test requires a feature that's not provided, or
                # requires the absence of a feature that is provided, no match.
                return False
        return True

    @classmethod
    def boottestflags(cls, **flags: bool) -> Self:
        provides: Self = cls(storage_boot=True,
                             storage_tp=True,
                             smp4=True)
        provides.set_provide(**flags)
        return provides

    @classmethod
    def androidtestflags(cls, **flags: bool) -> Self:
        provides: Self = cls(android=True,
                             storage_boot=True,
                             storage_full=True,
                             storage_tp=True,
                             smp4=True,
                             abl=True,
                             tablet=True)
        provides.set_provide(**flags)
        return provides


class TrustyArchiveBuildFile(object):
    """Copy a file to archive directory after a build."""
    def __init__(self, src, dest, optional):
        self.src = src
        self.dest = dest
        self.optional = optional


class TrustyTest(object):
    """Stores a pair of a test name and a command to run"""
    def __init__(self, name, command, enabled, port_type = PortType.TEST):
        self.name = name
        self.command = command
        self.enabled = enabled
        self.port_type = port_type

    def type(self, port_type):
        self.port_type = PortType(port_type)  # ensure we have a valid port type
        return self

class TrustyHostTest(TrustyTest):
    """Stores a pair of a test name and a command to run on host.

    TrustyHostTest is for tests that run solely on the host.
    This is different from TrustyHostcommandTest, which runs tests on
    the host that issue commands to a running Android device via adb.
    """

    class TrustyHostTestFlags:
        """Enable needs to be matched with provides without special casing"""

        @staticmethod
        def match_provide(_):
            # cause host test to be filtered out if they appear in a boottests
            # or androidportests environment which provides a set of features.
            return False

    need = TrustyHostTestFlags()


class TrustyAndroidTest(TrustyTest):
    """Stores a test name and command to run inside Android"""

    def __init__(self, name, command, need=None,
                 port_type=PortType.TEST, enabled=True, nameprefix="",
                 runargs=(), timeout=None):
        nameprefix = nameprefix + "android-test:"
        cmd = ["run", "--headless", "--shell-command", command]
        if timeout:
            cmd += ['--timeout', str(timeout)]
        if runargs:
            cmd += list(runargs)
        super().__init__(nameprefix + name, cmd, enabled, port_type)
        self.shell_command = command
        if need:
            self.need = need
        else:
            self.need = TrustyPortTestFlags()

    def needs(self, **need):
        self.need.set(**need)
        return self

    def into_androidtest(self, **_):
        """Allows Android tests to be used inside a androidtests block."""
        return self


class TrustyHostcommandTest(TrustyTest):
    """Stores a test name and command to run on the host.

    TrustyHostcommandTest runs tests on the host that issue commands to a
    running Android device via adb. This is different from TrustyHostTest,
    which is for tests that run solely on the host.
    """

    def __init__(self, name, command, need=None,
                 port_type=PortType.TEST, enabled=True, nameprefix="",
                 timeout=None):
        nameprefix = nameprefix + "hostcommandtest:"

        # cmd stores arguments that are passed to the test runner in qemu.py.
        # The first item in the list isn't used.
        cmd = [None, "--headless", "--host-command", command]
        if timeout:
            cmd += ['--timeout', str(timeout)]
        super().__init__(nameprefix + name, cmd, enabled, port_type)

        if need:
            self.need = need
        else:
            self.need = TrustyPortTestFlags()

    def needs(self, **need):
        self.need.set(**need)
        return self


class TrustyPortTest(TrustyTest):
    """Stores a trusty port name for a test to run."""

    def __init__(self, port, port_type=PortType.TEST, enabled=True,
                 timeout=None):
        super().__init__(port, None, enabled, port_type)
        self.port = port
        self.need = TrustyPortTestFlags()
        self.timeout = timeout

    def needs(self, **need):
        self.need.set(**need)
        return self

    def into_androidtest(self, cmdargs, **kwargs):
        cmdargs = list(cmdargs)
        cmd = " ".join(["/vendor/bin/trusty-ut-ctrl", self.port] + cmdargs)
        return TrustyAndroidTest(self.name, cmd, self.need, self.port_type,
                                 self.enabled, timeout=self.timeout, **kwargs)

    def into_bootporttest(self) -> TrustyTest:
        cmd = ["run", "--headless", "--boot-test", self.port]
        cmd += ['--timeout', str(self.timeout)] if self.timeout else []
        return TrustyTest("boot-test:" + self.port, cmd, self.enabled,
                          self.port_type)


class TrustyCommand:
    """Base class for all types of commands that are *not* tests"""

    def __init__(self, name):
        self.name = name
        self.enabled = True
        # avoids special cases in list_config
        self.command = []
        # avoids special cases in porttest_match
        self.need = TrustyPortTestFlags()

    def needs(self, **_):
        """Allows commands to be used inside a needs block."""
        return self

    def into_androidtest(self, **_):
        return self

    def into_bootporttest(self):
        return self


class RebootMode(StrEnum):
    REGULAR = "reboot"
    FACTORY_RESET = "reboot (with factory reset)"
    FULL_WIPE = "reboot (with full wipe)"

    def factory_reset(self) -> bool:
        """Whether this reboot includes a factory reset.
        This function exists because we can't make the test runner module depend
        on types defined here, so its function args have to be builtins.
        """
        match self:
            case RebootMode.REGULAR:
                return False
            case RebootMode.FACTORY_RESET:
                return True
            case RebootMode.FULL_WIPE:
                return True

    def full_wipe(self) -> bool:
        """Whether this reboot includes a an RPMB wipe.
        This function exists because we can't make the test runner module depend
        on types defined here, so its function args have to be builtins.
        """
        match self:
            case RebootMode.REGULAR:
                return False
            case RebootMode.FACTORY_RESET:
                return False
            case RebootMode.FULL_WIPE:
                return True


class AndroidBootPropertyBase(str):
    """Base class for Android boot properties."""


class AndroidBootProperty():
    """Android boot property collection class."""
    class MockBootCtrl():
        class UpdateToSlot(AndroidBootPropertyBase):
            """Mock boot control property to update to a slot."""
            def __new__(cls, slot: int):
                assert slot in (0, 1), f"slot must be 0 or 1, not {slot}"
                return super().__new__(cls, "androidboot.vendor.mock_boot_ctrl"
                                       f".update_to_slot={int(slot)}")


class TrustyRebootCommand(TrustyCommand):
    """Marker object which causes the test environment to be rebooted before the
       next test is run. Used to reset the test environment and to test storage.
    """
    def __init__(
            self, mode: RebootMode = RebootMode.FACTORY_RESET, *,
            androidbootproperties: Optional[list[AndroidBootProperty]] = None):
        if not isinstance(mode, RebootMode):
            raise TypeError("mode must be a RebootMode instance")
        if androidbootproperties is not None:
            if not isinstance(androidbootproperties, list):
                raise TypeError("androidbootproperties must be a list")
            for prop in androidbootproperties:
                if not issubclass(type(prop), AndroidBootPropertyBase):
                    raise TypeError(
                        "Items in androidbootproperties must be instances of "
                        "AndroidBootProperty subtypes"
                    )
        super().__init__(mode)
        self.mode = mode
        self.androidbootproperties = androidbootproperties

    def extra_linux_args(self) -> list[str]:
        if self.androidbootproperties is None:
            return []
        return [str(arg) for arg in self.androidbootproperties]

class TrustyPrintCommand(TrustyCommand):

    def msg(self) -> str:
        return self.name

class TrustyCompositeTest(TrustyTest):
    """Stores a sequence of tests that must execute in order"""

    def __init__(self, name: str,
                 sequence: List[TrustyPortTest | TrustyCommand],
                 enabled=True):
        super().__init__(name, [], enabled)
        self.sequence = sequence
        self.need = TrustyPortTestFlags()
        for subtest in sequence:
            self.need.set(**subtest.need.flags)

    def needs(self, **need):
        self.need.set(**need)
        return self

    def into_androidtest(self, **kwargs):
        # because the needs of the composite test is the union of the needs of
        # its subtests, we do not need to filter out any subtests; all needs met
        self.sequence = [subtest.into_androidtest(**kwargs)
                         for subtest in self.sequence]
        return self

    def into_bootporttest(self):
        # similarly to into_androidtest, we do not need to filter out tests
        self.sequence = [subtest.into_bootporttest()
                         for subtest in self.sequence]
        return self


class TrustyBuildConfig(object):
    """Trusty build and test configuration file parser."""

    def __init__(self, config_file=None, debug=False, android=None):
        """Inits TrustyBuildConfig.

        Args:
            config_file: Optional config file path. If omitted config file is
                found relative to script directory.
            debug: Optional boolean value. Set to True to enable debug messages.
        """
        self.debug = debug
        self.android = android
        self.projects = {}
        self.dist = []
        self.default_signing_keys = []
        self.doc_files = []

        if config_file is None:
            config_file = os.path.join(script_dir, "build-config")
        self.read_config_file(config_file)

    def read_config_file(self, path, optional=False):
        """Main parser function called constructor or recursively by itself."""
        if optional and not os.path.exists(path):
            if self.debug:
                print("Skipping optional config file:", path)
            return []

        if self.debug:
            print("Reading config file:", path)

        config_dir = os.path.dirname(path)

        def _flatten_list(inp, out):
            for obj in inp:
                if isinstance(obj, list):
                    _flatten_list(obj, out)
                else:
                    out.append(obj)

        def flatten_list(inp):
            out = []
            _flatten_list(inp, out)
            return out

        def include(path, optional=False):
            """Process include statement in config file."""
            if self.debug:
                print("include", path, "optional", optional)
            if path.startswith("."):
                path = os.path.join(config_dir, path)
            return self.read_config_file(path=path, optional=optional)

        def build(projects, enabled=True, dist=None):
            """Process build statement in config file."""
            for project_name in projects:
                if self.debug:
                    print("build", project_name, "enabled", enabled)
                project = self.get_project(project_name)
                project.build = bool(enabled)
            if dist:
                for item in dist:
                    assert isinstance(item, TrustyArchiveBuildFile), item
                    self.dist.append(item)

        def builddep(projects, needs):
            """Process build statement in config file."""
            for project_name in projects:
                project = self.get_project(project_name)
                for project_dep_name in needs:
                    project_dep = self.get_project(project_dep_name)
                    if self.debug:
                        print("build", project_name, "needs", project_dep_name)
                    project.also_build[project_dep_name] = project_dep

        def archive(src, dest=None, optional=False):
            return TrustyArchiveBuildFile(src, dest, optional)

        def testmap(projects, tests=()):
            """Process testmap statement in config file."""
            for project_name in projects:
                if self.debug:
                    print("testmap", project_name, "build", build)
                    for test in tests:
                        print(test)
                project = self.get_project(project_name)
                project.tests += flatten_list(tests)

        def hosttest(host_cmd, enabled=True, repeat=1):
            cmd = ["host_tests/" + host_cmd]
            # TODO: assumes that host test is always a googletest
            if repeat > 1:
                cmd.append(f"--gtest_repeat={repeat}")
            return TrustyHostTest("host-test:" + host_cmd, cmd, enabled)

        def hosttests(tests):
            return [test for test in flatten_list(tests)
                    if isinstance(test, TrustyHostTest)]

        def porttest_match(test, provides):
            return test.need.match_provide(provides)

        def porttests_filter(tests, provides):
            return [test for test in flatten_list(tests)
                    if porttest_match(test, provides)]

        def boottests(port_tests, provides=None):
            if provides is None:
                provides = TrustyPortTestFlags.boottestflags()
            return [test.into_bootporttest()
                    for test in porttests_filter(port_tests, provides)]

        def androidtests(port_tests, provides=None, nameprefix="",
                         cmdargs=(), runargs=()):
            # nameprefix is only used by TrustyPortTest.into_androidtest,
            # other into_androidtest ignore the nameprefix parameter
            nameprefix = nameprefix + "android-port-test:"
            if provides is None:
                provides = TrustyPortTestFlags.androidtestflags()

            return [test.into_androidtest(nameprefix=nameprefix,
                                              cmdargs=cmdargs,
                                              runargs=runargs)
                    for test in porttests_filter(port_tests, provides)]

        def needs(tests, *args, **kwargs):
            return [
                test.needs(*args, **kwargs)
                for test in flatten_list(tests)
            ]

        def devsigningkeys(
            default_key_paths: List[str],
            project_overrides: Optional[Dict[str, List[str]]] = None):
            self.default_signing_keys.extend(default_key_paths)
            if project_overrides is None:
                return

            for project_name, overrides in project_overrides.items():
                project = self.get_project(project_name)
                if project.signing_keys is None:
                    project.signing_keys = []
                project.signing_keys.extend(overrides)

        def docs(doc_files: List[str]):
            self.doc_files.extend(doc_files)

        file_format = {
            "BENCHMARK": PortType.BENCHMARK,
            "TEST": PortType.TEST,
            "include": include,
            "build": build,
            "builddep": builddep,
            "archive": archive,
            "testmap": testmap,
            "hosttest": hosttest,
            "porttest": TrustyPortTest,
            "compositetest": TrustyCompositeTest,
            "porttestflags": TrustyPortTestFlags,
            "androidtestflags": TrustyPortTestFlags.androidtestflags,
            "boottestflags": TrustyPortTestFlags.boottestflags,
            "hosttests": hosttests,
            "boottests": boottests,
            "androidtest": TrustyAndroidTest,
            "hostcommandtest": TrustyHostcommandTest,
            "androidtests": androidtests,
            "androidporttests": androidtests,
            "needs": needs,
            "reboot": TrustyRebootCommand,
            "RebootMode": RebootMode,
            "AndroidBootProperty": AndroidBootProperty,
            "devsigningkeys": devsigningkeys,
            "print": TrustyPrintCommand,
            "docs": docs,
        }

        with open(path, encoding="utf8") as f:
            code = compile(f.read(), path, "eval")
            config = eval(code, file_format)  # pylint: disable=eval-used
            return flatten_list(config)

    def get_project(self, project):
        """Return TrustyBuildConfigProject entry for a project."""
        if project not in self.projects:
            self.projects[project] = TrustyBuildConfigProject()
        return self.projects[project]

    def get_projects(self, build=None, have_tests=None):
        """Return a list of projects.

        Args:
            build: If True only return projects that should be built. If False
                only return projects that should not be built. If None return
                both projects that should be built and not be built. (default
                None).
            have_tests: If True only return projects that have tests. If False
                only return projects that don't have tests. If None return
                projects regardless if they have tests. (default None).
        """

        def match(item):
            """filter function for get_projects."""
            project = self.projects[item]

            return ((build is None or build == project.build) and
                    (have_tests is None or
                     have_tests == bool(project.tests)))

        return (project for project in sorted(self.projects.keys())
                if match(project))

    def signing_keys(self, project_name: str):
        project_specific_keys = self.get_project(project_name).signing_keys
        if project_specific_keys is None:
            return self.default_signing_keys
        return project_specific_keys


def list_projects(args):
    """Read config file and print a list of projects.

    See TrustyBuildConfig.get_projects for filtering options.

    Args:
        args: Program arguments.
    """
    config = TrustyBuildConfig(config_file=args.file, debug=args.debug)
    for project in sorted(config.get_projects(**dict(args.filter))):
        print(project)


def list_config(args):
    """Read config file and print all project and tests."""
    config = TrustyBuildConfig(config_file=args.file, debug=args.debug)
    print("Projects:")
    for project_name, project in sorted(config.projects.items()):
        print("  " + project_name + ":")
        print("    Build:", project.build)
        print("    Tests:")
        for test in project.tests:
            print("      " + test.name + ":")
            print("        " + str(test.command))

    for build in [True, False]:
        print()
        print("Build:" if build else "Don't build:")
        for tested in [True, False]:
            projects = config.get_projects(build=build, have_tests=tested)
            for project in sorted(projects):
                print("  " + project + ":")
                project_config = config.get_project(project)
                for test in project_config.tests:
                    print("    " + test.name)
            if projects and not tested:
                print("    No tests")


def any_test_name(regex, tests):
    """Checks the name of all tests in a list for a regex.

    This is intended only as part of the selftest facility, do not use it
    to decide how to consider actual tests.

    Args:
        tests: List of tests to check the names of
        regex: Regular expression to check them for (as a string)
    """

    return any(re.match(regex, test.name) is not None for test in tests)


def has_host(tests):
    """Checks for a host test in the provided tests by name.

    This is intended only as part of the selftest facility, do not use it
    to decide how to consider actual tests.

    Args:
        tests: List of tests to check for host tests
    """
    return any_test_name("host-test:", tests)


def has_unit(tests):
    """Checks for a unit test in the provided tests by name.

    This is intended only as part of the selftest facility, do not use it
    to decide how to consider actual tests.

    Args:
        tests: List of tests to check for unit tests
    """
    return any_test_name("boot-test:", tests)


def test_config(args):
    """Test config file parser.

    Uses a test config file where all projects have names that describe if they
    should be built and if they have tests.

    Args:
        args: Program arguments.
    """
    config_file = os.path.join(script_dir, "trusty_build_config_self_test_main")
    config = TrustyBuildConfig(config_file=config_file, debug=args.debug)

    projects_build = {}

    project_regex = re.compile(
        r"self_test\.build_(yes|no)\.tests_(none|host|unit|both)\..*")

    for build in [None, True, False]:
        projects_build[build] = {}
        for tested in [None, True, False]:
            projects = list(config.get_projects(build=build, have_tests=tested))
            projects_build[build][tested] = projects
            if args.debug:
                print("Build", build, "tested", tested, "count", len(projects))
            assert projects
            for project in projects:
                if args.debug:
                    print("-", project)
                m = project_regex.match(project)
                assert m
                if build is not None:
                    assert m.group(1) == ("yes" if build else "no")
                if tested is not None:
                    if tested:
                        assert (m.group(2) == "host" or
                                m.group(2) == "unit" or
                                m.group(2) == "both")
                    else:
                        assert m.group(2) == "none"

        assert(projects_build[build][None] ==
               sorted(projects_build[build][True] +
                      projects_build[build][False]))
    for tested in [None, True, False]:
        assert(projects_build[None][tested] ==
               sorted(projects_build[True][tested] +
                      projects_build[False][tested]))

    print("get_projects test passed")

    reboot_seen = False

    def check_test(i, test):
        match test:
            case TrustyTest():
                host_m = re.match(r"host-test:self_test.*\.(\d+)",
                                  test.name)
                unit_m = re.match(r"boot-test:self_test.*\.(\d+)",
                                  test.name)
                if args.debug:
                    print(project, i, test.name)
                m = host_m or unit_m
                assert m
                assert m.group(1) == str(i + 1)
            case TrustyRebootCommand():
                assert False, "Reboot outside composite command"
            case _:
                assert False, "Unexpected test type"

    def check_subtest(i, test):
        nonlocal reboot_seen
        match test:
            case TrustyRebootCommand():
                reboot_seen = True
            case _:
                check_test(i, test)

    for project_name in config.get_projects():
        project = config.get_project(project_name)
        if args.debug:
            print(project_name, project)
        m = project_regex.match(project_name)
        assert m
        kind = m.group(2)
        if kind == "both":
            assert has_host(project.tests)
            assert has_unit(project.tests)
        elif kind == "unit":
            assert not has_host(project.tests)
            assert has_unit(project.tests)
        elif kind == "host":
            assert has_host(project.tests)
            assert not has_unit(project.tests)
        elif kind == "none":
            assert not has_host(project.tests)
            assert not has_unit(project.tests)
        else:
            assert False, "Unknown project kind"

        for i, test in enumerate(project.tests):
            match test:
                case TrustyCompositeTest():
                    # because one of its subtest needs storage_boot and not
                    # storage_full, the composite test should need the same
                    assert test.need.flags.get("storage_boot", False)
                    assert not test.need.flags.get("storage_full", True)
                    for subtest in test.sequence:
                        check_subtest(i, subtest)
                case _:
                    check_test(i, test)

    assert reboot_seen

    print("get_tests test passed")


def main():
    top = os.path.abspath(os.path.join(script_dir, "../../../../.."))
    os.chdir(top)

    parser = argparse.ArgumentParser()
    parser.add_argument("-d", "--debug", action="store_true")
    parser.add_argument("--file")
    # work around for https://bugs.python.org/issue16308
    parser.set_defaults(func=lambda args: parser.print_help())
    subparsers = parser.add_subparsers()

    parser_projects = subparsers.add_parser("projects",
                                            help="list project names")

    group = parser_projects.add_mutually_exclusive_group()
    group.add_argument("--with-tests", action="append_const",
                       dest="filter", const=("have_tests", True),
                       help="list projects that have tests")
    group.add_argument("--without-tests", action="append_const",
                       dest="filter", const=("have_tests", False),
                       help="list projects that don't have tests")

    group = parser_projects.add_mutually_exclusive_group()
    group.add_argument("--all", action="append_const",
                       dest="filter", const=("build", None),
                       help="include disabled projects")
    group.add_argument("--disabled", action="append_const",
                       dest="filter", const=("build", False),
                       help="only list disabled projects")
    parser_projects.set_defaults(func=list_projects, filter=[("build", True)])

    parser_config = subparsers.add_parser("config", help="dump config")
    parser_config.set_defaults(func=list_config)

    parser_config = subparsers.add_parser("selftest", help="test config parser")
    parser_config.set_defaults(func=test_config)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
