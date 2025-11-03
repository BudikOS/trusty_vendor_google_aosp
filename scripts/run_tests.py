#!/bin/sh
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
""":" # Shell script (in docstring to appease pylint)

# Find and invoke hermetic python3 interpreter
. "`dirname $0`/envsetup.sh"; exec "$PY3" "$0" "$@"
# Shell script end
Run tests for a project.
"""

import argparse
from enum import Enum
import importlib
import os
import re
import subprocess
import sys
import time
from typing import Optional

from trusty_build_config import PortType, TrustyCompositeTest, TrustyTest
from trusty_build_config import TrustyAndroidTest, TrustyBuildConfig
from trusty_build_config import TrustyHostTest, TrustyRebootCommand
from trusty_build_config import TrustyPrintCommand
from trusty_build_config import TrustyHostcommandTest


TEST_STATUS = Enum("TEST_STATUS", ["PASSED", "FAILED", "SKIPPED"])

class TestResult:
    """Stores results for a single test.

    Attributes:
        test: Name of the test.
        status: Test's integer return code, or None if this test was skipped.
        retried: True if this test was retried.
    """
    test: str
    status: Optional[int]
    retried: bool

    def __init__(self, test: str, status: Optional[int], retried: bool):
        self.test = test
        self.status = status
        self.retried = retried

    def test_status(self) -> TEST_STATUS:
        if self.status is None:
            return TEST_STATUS.SKIPPED
        return TEST_STATUS.PASSED if self.status == 0 else TEST_STATUS.FAILED

    def failed(self) -> bool:
        return self.test_status() == TEST_STATUS.FAILED

    def __format__(self, _format_spec: str) -> str:
        return f"{self.test:s} returned {self.status:d}"


class TestResults(object):
    """Stores test results.

    Attributes:
        project: Name of project that tests were run on.
        passed: True if all tests passed, False if one or more tests failed.
        passed_count: Number of tests passed.
        failed_count: Number of tests failed.
        flaked_count: Number of tests that failed then passed on second try.
        retried_count: Number of tests that were given a second try.
        test_results: List of tuples storing test name an status.
    """

    def __init__(self, project):
        """Inits TestResults with project name and empty test results."""
        self.project = project
        self.passed = True
        self.passed_count = 0
        self.failed_count = 0
        self.skipped_count = 0
        self.flaked_count = 0
        self.retried_count = 0
        self.test_results = []

    def add_result(self, result: TestResult):
        """Add a test result."""
        self.test_results.append(result)
        if result.test_status() == TEST_STATUS.PASSED:
            self.passed_count += 1
            if result.retried:
                self.flaked_count += 1
        elif result.test_status() == TEST_STATUS.FAILED:
            self.failed_count += 1
            self.passed = False
        elif result.test_status() == TEST_STATUS.SKIPPED:
            self.skipped_count += 1

        if result.retried:
            self.retried_count += 1

    def print_results(self, print_failed_only=False):
        """Print test results."""
        if print_failed_only:
            if self.passed:
                return
            sys.stdout.flush()
            out = sys.stderr
        else:
            out = sys.stdout
        test_count = self.passed_count + self.failed_count + self.skipped_count
        test_attempted = self.passed_count + self.failed_count
        out.write(
            "\n"
            f"There were {test_count} defined for project {self.project}.\n"
            f"{test_attempted} ran and {self.skipped_count} were skipped."
        )
        if test_count:
            for result in self.test_results:
                match (result.test_status(), result.retried, print_failed_only):
                    case (TEST_STATUS.FAILED, _, _):
                        out.write(f"[  FAILED  ] {result.test}\n")
                    case (TEST_STATUS.SKIPPED, _, False):
                        out.write(f"[  SKIPPED ] {result.test}\n")
                    case (TEST_STATUS.PASSED, retried, False):
                        out.write(f"[       OK ] {result.test}\n")
                        if retried:
                            out.write(
                                f"WARNING: {result.test} was re-run and "
                                "passed on second try; it may be flaky\n"
                            )

            out.write(
                f"[==========] {test_count} tests ran for project "
                f"{self.project}.\n"
            )
            if self.passed_count and not print_failed_only:
                out.write(f"[  PASSED  ] {self.passed_count} tests.\n")
            if self.failed_count:
                out.write(f"[  FAILED  ] {self.failed_count} tests.\n")
            if self.skipped_count:
                out.write(f"[  SKIPPED ] {self.skipped_count} tests.\n")
            if self.flaked_count > 0:
                out.write(
                    f"WARNING: {self.flaked_count} tests passed when "
                    "re-run which indicates that they may be flaky.\n"
                )
            if self.retried_count == MAX_RETRIES:
                out.write(
                    f"WARNING: hit MAX_RETRIES({MAX_RETRIES}) during "
                    "testing after which point, no tests were retried.\n"
                )


class MultiProjectTestResults:
    """Stores results from testing multiple projects.

    Attributes:
        test_results: List containing the results for each project.
        failed_projects: List of projects with test failures.
        tests_passed: Count of test passes across all projects.
        tests_failed: Count of test failures across all projects.
        had_passes: Count of all projects with any test passes.
        had_failures: Count of all projects with any test failures.
    """

    def __init__(self, test_results: list[TestResults]):
        self.test_results = test_results
        self.failed_projects = []
        self.tests_passed = 0
        self.tests_failed = 0
        self.tests_skipped = 0
        self.had_passes = 0
        self.had_failures = 0
        self.had_skip = 0

        for result in self.test_results:
            if not result.passed:
                self.failed_projects.append(result.project)
            self.tests_passed += result.passed_count
            self.tests_failed += result.failed_count
            self.tests_skipped += result.skipped_count
            if result.passed_count:
                self.had_passes += 1
            if result.failed_count:
                self.had_failures += 1
            if result.skipped_count:
                self.had_skip += 1

    def print_results(self):
        """Prints the test results to stdout and stderr."""
        for test_result in self.test_results:
            test_result.print_results()

        sys.stdout.write("\n")
        if self.had_passes:
            sys.stdout.write(
                f"[  PASSED  ] {self.tests_passed} tests in "
                f"{self.had_passes} projects.\n"
            )
        if self.had_failures:
            sys.stdout.write(
                f"[  FAILED  ] {self.tests_failed} tests in "
                f"{self.had_failures} projects.\n"
            )
            sys.stdout.flush()
        if self.had_skip:
            sys.stdout.write(
                f"[  SKIPPED ] {self.tests_skipped} tests in "
                f"{self.had_skip} projects.\n"
            )
            sys.stdout.flush()

            # Print the failed tests again to stderr as the build server will
            # store this in a separate file with a direct link from the build
            # status page. The full build long page on the build server, buffers
            # stdout and stderr and interleaves them at random. By printing
            # the summary to both stderr and stdout, we get at least one of them
            # at the bottom of that file.
            for test_result in self.test_results:
                test_result.print_results(print_failed_only=True)
            sys.stderr.write(
                f"[  FAILED  ] {self.tests_failed,} tests in "
                f"{self.had_failures} projects.\n"
            )


def test_should_run(testname: str, test_filters: Optional[list[re.Pattern]]):
    """Check if test should run.

    Args:
        testname: Name of test to check.
        test_filters: Regex list that limits the tests to run.

    Returns:
        True if test_filters list is empty or None, True if testname matches any
        regex in test_filters, False otherwise.
    """
    if not test_filters:
        return True
    for r in test_filters:
        if r.search(testname):
            return True
    return False


def projects_to_test(
    build_config: TrustyBuildConfig,
    projects: list[str],
    test_filters: list[re.Pattern],
    run_disabled_tests: bool = False,
) -> list[str]:
    """Checks which projects have any of the specified tests.

    Args:
        build_config: TrustyBuildConfig object.
        projects: Names of the projects to search for active tests.
        test_filters: List that limits the tests to run. Projects without any
          tests that match a filter will be skipped.
        run_disabled_tests: Also run disabled tests from config file.

    Returns:
        A list of projects with tests that should be run
    """

    def has_test(name: str):
        project = build_config.get_project(name)
        for test in project.tests:
            if not test.enabled and not run_disabled_tests:
                continue
            if test_should_run(test.name, test_filters):
                return True
        return False

    return [project for project in projects if has_test(project)]


# Put a global cap on the number of retries to detect flaky tests such that we
# do not risk increasing the time to try all tests substantially. This should be
# fine since *most* tests are not flaky.
# TODO: would it be better to put a cap on the time spent retrying tests? We may
#       not want to retry long running tests.
MAX_RETRIES = 10


def run_tests(
    build_config: TrustyBuildConfig,
    root: os.PathLike,
    project: str,
    qemu_instance_id: Optional[str],
    run_disabled_tests: bool = False,
    test_filters: Optional[list[re.Pattern]] = None,
    verbose: bool = False,
    debug_on_error: bool = False,
    emulator: bool = True,
) -> TestResults:
    """Run tests for a project.

    Args:
        build_config: TrustyBuildConfig object.
        root: Trusty build root output directory.
        project: Project name.
        qemu_instance_id: name of the QEmu instance to use. If the instance
            doesn't already exist, a new fresh instance will be created. If
            None, use the default instance.
        run_disabled_tests: Also run disabled tests from config file.
        test_filters: Optional list that limits the tests to run.
        verbose: Enable debug output.
        debug_on_error: Wait for debugger connection on errors.

    Returns:
        TestResults object listing overall and detailed test results.
    """
    project_config = build_config.get_project(project=project)
    project_root = f"{root}/build-{project}"

    test_results = TestResults(project)
    test_env = None
    test_runner = None
    extra_linux_args: list[str] = []

    if not qemu_instance_id:
        qemu_instance_id = "default"
    qemu_instance_dir = f"{project_root}/qemu-instances/{qemu_instance_id}"

    def load_test_environment():
        sys.path.append(project_root)
        try:
            if run := sys.modules.get("run"):
                if not run.__file__.startswith(project_root):
                    # Reload qemu and its dependencies because run.py uses them
                    # We do this in topological sort order
                    if qemu_error := sys.modules.get("qemu_error"):
                        importlib.reload(qemu_error)
                    if qemu_options := sys.modules.get("qemu_options"):
                        importlib.reload(qemu_options)
                    if qemu := sys.modules.get("qemu"):
                        importlib.reload(qemu)

                    # run module was imported for another project and needs
                    # to be replaced with the one for the current project.
                    run = importlib.reload(run)
            else:
                # first import in this interpreter instance, we use importlib
                # rather than a regular import statement since it avoids
                # linter warnings.
                run = importlib.import_module("run")
            sys.path.pop()
        except ImportError:
            return None

        return run

    def print_test_command(name, cmd: Optional[list[str]] = None):
        print()
        print("Running", name, "on", test_results.project)
        if cmd:
            print(
                "Command line:", " ".join([s.replace(" ", "\\ ") for s in cmd])
            )
        sys.stdout.flush()

    def run_test(
        test, parent_test: Optional[TrustyCompositeTest] = None, retry=True
    ) -> Optional[TestResult]:
        """Execute a single test and print out helpful information

        Returns:
            The results of running this test, or None for non-tests, like
            reboots or tests that don't work in this environment.
        """
        nonlocal extra_linux_args, test_env, test_runner
        cmd = test.command[1:]
        disable_rpmb = True if "--disable_rpmb" in cmd else None

        test_start_time = time.time()

        if not emulator and not isinstance(test, TrustyHostTest):
            return None

        match test:
            case TrustyHostTest():
                # append nice and expand path to command
                cmd = ["nice", f"{project_root}/{test.command[0]}"] + cmd
                print_test_command(test.name, cmd)
                cmd_status = subprocess.call(cmd)
                result = TestResult(test.name, cmd_status, False)
            case TrustyCompositeTest():
                status_code: Optional[int] = 0
                for subtest in test.sequence:
                    subtest_result = run_test(subtest, test, retry)
                    if subtest_result and subtest_result.failed():
                        status_code = subtest_result.status
                        # fail the composite test with the same status code as
                        # the first failing subtest
                        break
                result = TestResult(test.name, status_code, False)

            case TrustyTest():
                # Benchmark runs on QEMU are meaningless and take a lot of
                # CI time. One can still run the bootport test manually
                # if desired
                if test.port_type == PortType.BENCHMARK:
                    return TestResult(test.name, None, False)
                else:
                    if isinstance(test, TrustyAndroidTest):
                        print_test_command(test.name, [test.shell_command])
                    elif isinstance(test, TrustyHostcommandTest):
                        print_test_command(test.name, test.command[3:])
                    else:
                        # port tests are identified by their port name,
                        # no command
                        print_test_command(test.name)

                    if not test_env:
                        test_env = load_test_environment()
                    if test_env:
                        if not test_runner:
                            test_runner = test_env.init(
                                android=build_config.android,
                                instance_dir=qemu_instance_dir,
                                disable_rpmb=disable_rpmb,
                                verbose=verbose,
                                debug_on_error=debug_on_error,
                                extra_linux_args=extra_linux_args,
                            )
                        cmd_status = test_env.run_test(test_runner, cmd)
                        result = TestResult(test.name, cmd_status, False)
                    else:
                        return TestResult(test.name, None, False)
            case TrustyRebootCommand() if parent_test:
                assert isinstance(parent_test, TrustyCompositeTest)
                extra_linux_args = test.extra_linux_args()
                if test_env:
                    # Note that since the mode evaluated as part of the shutdown
                    # of the previous runner instead of the new one, the mode
                    # will be ignored if there is no existing test_env and
                    # runner. This means that while a composite test can start
                    # with a reboot command, e.g. so it can request
                    # extra_linux_args, requesting a non-default mode is not
                    # currently supported as it will lead to inconsistent
                    # behavior between indivial test runs and runs where other
                    # tests have been run before.
                    test_env.shutdown(test_runner, test.mode.factory_reset(),
                                      full_wipe=test.mode.full_wipe())
                    test_runner = None
                    print(f"Shutting down to {test.mode} test environment on "
                          f"{test_results.project}")
                # return early so we do not report the time to reboot or try to
                # add the reboot command to test results.
                return None
            case TrustyRebootCommand():
                raise RuntimeError(
                    "Reboot may only be used inside compositetest"
                )
            case TrustyPrintCommand() if parent_test:
                print(test.msg())
                return None
            case TrustyPrintCommand():
                raise RuntimeError(
                    "Print may only be used inside compositetest"
                )
            case _:
                raise NotImplementedError(f"Don't know how to run {test.name}")

        elapsed = time.time() - test_start_time
        print( f"{result} after {elapsed:.3f} seconds")

        can_retry = retry and test_results.retried_count < MAX_RETRIES
        if result and result.failed() and can_retry:
            print(
                f"retrying potentially flaky test {test.name} on",
                test_results.project,
            )
            # TODO: first retry the test without restarting the test
            #       environment and if that fails, restart and then
            #       retry if < MAX_RETRIES.
            if test_env:
                test_env.shutdown(test_runner)
                test_runner = None
            retried_result = run_test(test, parent_test, retry=False)
            # Know this is the kind of test that returns a status b/c it failed
            assert retried_result is not None
            retried_result.retried = True
            return retried_result
        else:
            # Test passed, was skipped, or we're not retrying it.
            return result

    # the retry mechanism is intended to allow a batch run of all tests to pass
    # even if a small handful of tests exhibit flaky behavior. If a test filter
    # was provided or debug on error is set, we are most likely not doing a
    # batch run (as is the case for presubmit testing) meaning that it is
    # not all that helpful to retry failing tests vs. finishing the run faster.
    retry = test_filters is None and not debug_on_error
    try:
        for test in project_config.tests:
            if not test.enabled and not run_disabled_tests:
                continue
            if not test_should_run(test.name, test_filters):
                continue

            if result := run_test(test, None, retry):
                test_results.add_result(result)
    finally:
        # finally is used here to make sure that we attempt to shutdown the
        # test environment no matter whether an exception was raised or not
        # and no matter what kind of test caused an exception to be raised.
        if test_env:
            test_env.shutdown(test_runner)
        # any saved exception from the try block will be re-raised here

    return test_results


def test_projects(
    build_config: TrustyBuildConfig,
    root: os.PathLike,
    projects: list[str],
    qemu_instance_id: Optional[str] = None,
    run_disabled_tests: bool = False,
    test_filters: Optional[list[re.Pattern]] = None,
    verbose: bool = False,
    debug_on_error: bool = False,
    emulator: bool = True,
) -> MultiProjectTestResults:
    """Run tests for multiple project.

    Args:
        build_config: TrustyBuildConfig object.
        root: Trusty build root output directory.
        projects: Names of the projects to run tests for.
        qemu_instance_id: name of the QEmu instance to use. If the instance
            doesn't already exist, a new fresh instance will be created. If
            None, use the default instance.
        run_disabled_tests: Also run disabled tests from config file.
        test_filters: Optional list that limits the tests to run. Projects
          without any tests that match a filter will be skipped.
        verbose: Enable debug output.
        debug_on_error: Wait for debugger connection on errors.

    Returns:
        MultiProjectTestResults listing overall and detailed test results.
    """
    if test_filters:
        projects = projects_to_test(
            build_config,
            projects,
            test_filters,
            run_disabled_tests=run_disabled_tests,
        )

    results = []
    for project in projects:
        results.append(
            run_tests(
                build_config,
                root,
                project,
                qemu_instance_id=qemu_instance_id,
                run_disabled_tests=run_disabled_tests,
                test_filters=test_filters,
                verbose=verbose,
                debug_on_error=debug_on_error,
                emulator=emulator,
            )
        )
    return MultiProjectTestResults(results)


def default_root() -> str:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    top = os.path.abspath(os.path.join(script_dir, "../../../../.."))
    return os.path.join(top, "build-root")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "project", type=str, nargs="+", help="Project(s) to test."
    )
    parser.add_argument(
        "--instance-id",
        type=str,
        default=None,
        help=("ID of a QEmu instance to use for the tests. A fresh instance "
              "will be created if no instance with this ID already exists."
              "'default' will be used if no value is provided.")
    )
    parser.add_argument(
        "--build-root",
        type=str,
        default=default_root(),
        help="Root of intermediate build directory.",
    )
    parser.add_argument(
        "--run_disabled_tests",
        help="Also run disabled tests from config file.",
        action="store_true",
    )
    parser.add_argument(
        "--test",
        type=str,
        action="append",
        help="Only run tests that match the provided regexes.",
    )
    parser.add_argument(
        "--verbose", help="Enable debug output.", action="store_true"
    )
    parser.add_argument(
        "--debug_on_error",
        help="Wait for debugger connection on errors.",
        action="store_true",
    )
    parser.add_argument(
        "--android",
        type=str,
        help="Path to an Android build to run tests against.",
    )
    args = parser.parse_args()

    build_config = TrustyBuildConfig(android=args.android)

    test_filters = (
        [re.compile(test) for test in args.test] if args.test else None
    )
    test_results = test_projects(
        build_config,
        args.build_root,
        args.project,
        qemu_instance_id=args.instance_id,
        run_disabled_tests=args.run_disabled_tests,
        test_filters=test_filters,
        verbose=args.verbose,
        debug_on_error=args.debug_on_error,
    )
    test_results.print_results()

    if test_results.failed_projects:
        sys.exit(1)


if __name__ == "__main__":
    main()
