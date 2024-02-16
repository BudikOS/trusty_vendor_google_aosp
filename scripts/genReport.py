#
# Copyright (C) 2023 The Android Open Source Project
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

"""
This script helps to generate source based code coverage report.

Usage:
    python genReport.py --objects [OBJECTS] --format [FORMAT]

"""

import argparse
import os
import subprocess
import sys
import pathlib

def genProfdata(files, llvmDir):
    llvmProfdataBin = str(str(os.path.join(llvmDir, "./llvm-profdata")))
    subprocess_cmd = [llvmProfdataBin, 'merge']

    subprocess_cmd.extend(files)
    subprocess_cmd.extend(['-o=out.profdata'])

    subprocess.call(subprocess_cmd)

def genHtml(llvmDir, objects, out):
    llvmCovBin = str(str(os.path.join(llvmDir, "./llvm-cov")))
    subprocess_cmd = [llvmCovBin, 'show', '-instr-profile=out.profdata', '-object']

    subprocess_cmd.extend(objects)
    subprocess_cmd.extend(['-use-color', '--format=html'])
    with(open(out+".html", "w")) as f:
        output = subprocess.call(subprocess_cmd, stdout=f)

def genJson(llvmDir, objects, out):
    llvmCovBin = str(str(os.path.join(llvmDir, "./llvm-cov")))
    subprocess_cmd = [llvmCovBin, 'export', '-summary-only',
                       '-instr-profile=out.profdata', '-object']

    subprocess_cmd.extend(objects)
    with(open(out+".json", "w")) as f:
        output = subprocess.call(subprocess_cmd, stdout=f)


def main():
    arg_parser = argparse.ArgumentParser()

    arg_parser.add_argument(
        '--objects',
        type=str,
        required=True,
        nargs='+',
        help='List the elf files which are included in the test')

    arg_parser.add_argument(
        '--format',
        type=str,
        default='html',
        help='Output format of the "llvm-cov show/export" command. The '
        'supported formats are "text", "html" and "lcov".')

    arg_parser.add_argument(
        '--llvm-dir',
        type=str,
        default="prebuilts/clang/host/linux-x86/llvm-binutils-stable/",
        help='Provide path to LLVM binary directory to override the default one')

    arg_parser.add_argument(
        '--profraw-dir',
        type=str,
        default="tmp/",
        help='Provide path to directory containing .profraw files')

    arg_parser.add_argument(
        '--output',
        type=str,
        default="out",
        help='provide output filename(without extension)')


    args = arg_parser.parse_args()

    if (not os.path.isdir(args.llvm_dir)):
        print("Provide path to LLVM binary directory")
        return

    if (not os.path.isdir(args.profraw_dir)):
        print("Provide path to directory containing .profraw files")
        return

    profrawFiles = [str(os.path.join(args.profraw_dir, f))
                        for f in os.listdir(args.profraw_dir) if f.endswith('.profraw')]
    if (len(profrawFiles) == 0):
        print("No profraw files found in directory " + args.profraw_dir)

    genProfdata(profrawFiles, args.llvm_dir)

    if (args.format == "html"):
        genHtml(args.llvm_dir, args.objects, args.output)

    elif (args.format == "json"):
        genJson(args.llvm_dir, args.objects, args.output)

    else:
        print("Only json and html supported")
        return

if __name__ == '__main__':
    sys.exit(main())