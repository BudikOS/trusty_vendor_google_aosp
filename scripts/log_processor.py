#
# Copyright (C) 2024 The Android Open Source Project
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
"""Log processing utilities"""

import errno
import os
import pty
import re
import time
import shutil
import threading

class ModuleQueue:
    """Class representing a queue of modules being built

    There are 2 types of log messages coming from the
    underlying build subprocess:
    - 'start' message determining that some task for the module
        is started
    - 'done' message determining that some task for the module
        is finished

    Then 'start' message comes, the task is stored in ModuleStatus
    pending tasks set of the corresponding module in 'active' map.

    The module itself is also pushed to front in 'queued'.

    Module's status message is updated to the new started task name.

    Then 'done' message comes, it is looked up in corresponding module
    pending tasks set and popped out.

    If it was the last task of the module, its name is displayed
    as a status message of the module with 'done' mark. Otherwise,
    next pending task is displayed as a status message.

    Some messages can be 'start' and 'done' at the same time
    (e.g. checkpoint messages). They are popped out immediately,
    but update the status message.

    """

    class ModuleStatus:
        """Class representing module build status

        Used to store a set of pending tasks for each module
        and displaying it as a pinned status lines in colored build
        log.

        message: the message displayed as module status
        pending: map of pending (unfinished) tasks for the module,
                 if there are no pending tasks for a module,
                 it is treated as done
        """
        def __init__(self):
            self.message = ""
            self.pending = {}

        def done(self):
            return not self.pending

    def __init__(self):
        self.queued = []
        self.active = {}

    def len(self):
        diagnostic = "lengths of queued and active should be always equal"
        assert len(self.queued) == len(self.active), diagnostic
        return len(self.queued)

    def insert(self, key, val: tuple[str, bool]):
        """Insert/update a task in module status"""
        if key in self.active:
            # remove elem from the queue to bring it to the top
            self.queued.remove(key)
        else:
            # allocate new module status entry
            self.active[key] = self.ModuleStatus()
        self.queued.append(key)

        message, done = val
        active_entry = self.active[key]

        if done:
            done_time = 0

            if not message:
                # consider all pending tasks done
                active_entry.pending.clear()
            elif message in active_entry.pending:
                # remove from pending
                done_time = time.time() - active_entry.pending[message]
                active_entry.pending.pop(message)

            # update module status message
            if active_entry.done():
                # show last done message if done
                active_entry.message = message, done_time, done
            else:
                # show first pending message if any
                message, start_time = list(active_entry.pending.items())[0]
                active_entry.message = message, start_time, False
        else:
            start_time = time.time()
            # add task to pending (record its start time)
            active_entry.pending[message] = start_time
            active_entry.message = message, start_time, done

    def pop_front(self):
        """Finds a done module to pop from the queue"""
        # find first done entry
        pop = None
        for m in self.queued:
            if self.active[m].done():
                pop = m
                self.queued.remove(m)
                break

        if pop is None:
            # pops nothing if all remaining modules
            # are still building (have at least one pending task)
            return None, None

        ret = pop, self.active[pop].message

        # remove also from the dict
        self.active.pop(pop)

        return ret

class LogProcessor(threading.Thread):
    """Thread for processing build output

    log_*: markers inserted by build system that help
        parsing the log, should be the same as defined in
        extern/lk/make/macro.mk
    pinned_num: number of pinned status lines in build output
    stream: build output stream to process
    err: Determines which build output stream is attached
        True for stderr, False for stdout
    pinned: set of ModuleStatus objects that are currently processed
    lock: lock to dispatch output from 2 pipe threads (stdout/stderr)
    colored: a flag for colored output
    log_file: optional file handle to duplicate the output
    ansi_escape: regular expression to apply on output before
        putting it to the log file (removes clang/rustc colors)

    """

    log_prefix = "@log@"
    log_done = "@done@"
    log_sdone = "@sdone@"
    log_print = "@print@"
    log_separator = "@:@"

    pinned_num = 10

    class color:
        """ANSI escape codes for colored output"""
        red = "\x1b[31;20m"
        bold_red = "\x1b[31;1m"
        green = "\x1b[32;20m"
        bold_green = "\x1b[32;1m"
        yellow = "\x1b[33;20m"
        bold_yellow = "\x1b[33;1m"
        magenta = "\x1b[35;20m"
        bold_magenta = "\x1b[35;1m"
        cyan = "\x1b[36;20m"
        bold_cyan = "\x1b[36;1m"
        white = "\x1b[37;20m"
        bold_white = "\x1b[37;1m"
        grey = "\x1b[38;20m"
        reset = "\x1b[0m"

    def __init__(self, stream, err, pinned, lock, colored, log_file):
        threading.Thread.__init__(self)

        self.stream = stream
        self.err = err
        self.pinned = pinned
        self.lock = lock
        self.colored = colored
        self.log_file = log_file
        self.ansi_escape = re.compile(r'(\x9B|\x1B\[)[0-?]*[ -\/]*[@-~]')

        self.daemon = False

    def colorize(self, string, color):
        """Adds color to a string"""
        return color + string + self.color.reset if self.colored else string

    def display(self, module_status, entry, pinned):
        """Displays a message on terminal"""
        module, status = module_status
        message, start_time, done = entry

        if pinned:
            # know actual terminal size, so can adjust status line length
            term_width, _ = shutil.get_terminal_size((80, 24))

            # make sure whole line fits into a limit (max_total_width)
            max_module_width = term_width // 2
            if len(module) > max_module_width:
                module = "..." + module[-max_module_width + 3:]

            mwidth = term_width - len(module) - len(status) - 5 - 10
            if mwidth - 3 <= 0:
                # too small terminal window?
                message = ""
                status = status[:mwidth]
            elif len(message) > mwidth:
                message = "..." + message[-mwidth + 3:]

            # color the message
            module = self.colorize(module, self.color.bold_green)
            status = self.colorize(status, self.color.green)
            if done:
                message += " " + self.colorize(
                    f'{start_time:3.1f}s done', self.color.green)
            else:
                running_time = time.time() - start_time
                message += ' ' + self.colorize(
                    f'{running_time:3.1f}s', self.color.yellow)

        print(f'{module}: {status} {message}')

    def erase_pinned(self):
        """Erases pinned status lines from the terminal"""
        # scroll back to the beginning of pinned messages
        # so, next message will print over
        nerase = self.pinned_num
        print(f'\033[{nerase}A', end = '')

        # erase pinned messages before printing new stuff over
        print('\033[0J', end = '')

    def print_pinned(self):
        """Prints self.pinned_num pinned messages to the terminal"""

        # drop some old entries if it is too long
        while self.pinned.len() > self.pinned_num:
            # pop old done entries from pinned list
            module_status, _ = self.pinned.pop_front()
            if module_status is None:
                # no more entries to pop, leave it as is
                break

        i = 0
        for mstatus in self.pinned.queued:
            if i == self.pinned_num:
                break
            self.display(mstatus, self.pinned.active[mstatus].message, True)
            i = i + 1

        if i < self.pinned_num:
            # padd with empty lines
            for i in range(self.pinned_num - self.pinned.len()):
                print("")

    def process(self, long_line, error):
        """Processes a log message from the build system"""
        self.lock.acquire()

        # look for a special marker in output
        marker_index = long_line.find(self.log_prefix)
        if marker_index != -1:
            # sometimes multiple log lines are glued together
            # for some reason, split it here
            lines = long_line[marker_index:].split(self.log_prefix)
            for line in lines:
                if line[:len(self.log_print)] == self.log_print:
                    line = line[len(self.log_print):]
                    # print marker, print immediately
                    if self.log_file is not None:
                        self.log_file.write(line)

                    line = self.colorize(line, self.color.bold_cyan)
                    self.erase_pinned()
                    print(line, end = "")
                    self.print_pinned()
                    continue

                line = line.strip()

                if not line:
                    continue

                # check if a done marker inserted
                done = False
                silent = False
                if line[:len(self.log_done)] == self.log_done:
                    line = line[len(self.log_done):]
                    done = True
                if line[:len(self.log_sdone)] == self.log_sdone:
                    line = line[len(self.log_sdone):]
                    done = True
                    silent = True

                parsed = line.split(self.log_separator, 2)

                if len(parsed) != 3:
                    # malformed message
                    continue

                module, status, message = parsed
                if not module:
                    # log stream corrupted? Assign to 'root' module
                    module = "root"

                self.pinned.insert((module, status), (message, done))

                if self.log_file is not None:
                    # put all messages to the log file as they go
                    if not silent:
                        self.log_file.write(f'{module}: {status} {message}\n')
        else: # no markers
            line = long_line
            if error:
                # put additional colors on error stream messages
                if self.ansi_escape.search(line) is not None:
                    # if output is from clang or rustc and has colors already,
                    # keep it as is
                    pass
                elif (pos := line.lower().find('error')) != -1:
                    line = self.colorize(line[:pos], self.color.bold_white) + \
                           self.colorize('error', self.color.bold_red) + \
                           self.colorize(line[pos + len('error'):],
                                         self.color.bold_white)
                elif (pos := line.lower().find('warning')) != -1:
                    line = self.colorize(line[:pos], self.color.bold_white) + \
                           self.colorize('warning', self.color.bold_magenta) + \
                           self.colorize(line[pos + len('warning'):],
                                         self.color.bold_white)
                else:
                    line = self.colorize(line, self.color.bold_white)

            self.erase_pinned()
            print(line, end = "")
            self.print_pinned()

            if self.log_file is not None:
                # just print the message as is
                # make sure no escape (color) characters go to the log file
                line = self.ansi_escape.sub('', line)
                self.log_file.write(line)

        self.lock.release()

    def refresh(self):
        """Reprints pinned messages"""
        self.lock.acquire()
        self.erase_pinned()
        self.print_pinned()
        self.lock.release()

    def run(self):
        try:
            with open(self.stream, encoding="utf-8") as stream:
                for line in stream:
                    if not line:
                        break
                    self.process(line, self.err)
        except OSError as e:
            if errno.EIO == e.errno:
                # EIO also means EOF, maybe just the pipe is closed
                # on the other side
                pass
            else:
                raise

class LogEngine():
    """Log processing engine"""

    class RepeatTimer(threading.Timer):
        """Thread for repeatedly refreshing pinned messages"""
        def run(self):
            while not self.finished.wait(self.interval):
                self.function(*self.args, **self.kwargs)

    def __init__(self, log_file, colored=True):
        # a queue where all pinned log lines are stored
        pinned = ModuleQueue()

        # lock to sync output between threads (stdout and stderr)
        lock = threading.Lock()

        self.log_file = log_file

        # create pty buffers to capture output from the subprocess
        # this is necessary to keep clang and rustc colored output
        master_err, self.stderr = pty.openpty()
        master_out, self.stdout = pty.openpty()

        self.outThread = LogProcessor(stream=master_out, err=False,
            pinned=pinned, lock=lock, colored=colored, log_file=self.log_file)
        self.errThread = LogProcessor(stream=master_err, err=True,
            pinned=pinned, lock=lock, colored=colored, log_file=self.log_file)

        self.refresher = self.RepeatTimer(0.1, self.outThread.refresh)

    def __enter__(self):
        # no pinned entries yet, but print it now to allocate space
        self.outThread.print_pinned()

        self.outThread.start()
        self.errThread.start()

        # launch a pinned messages refreshing task
        self.refresher.start()
        return self

    def __exit__(self, exception_type, value, traceback):
        # stop log processor and refresher threads
        self.refresher.cancel()

        # make sure the latest pinned messages are printed
        self.outThread.refresh()

        # close remaining pipe ports
        os.close(self.stdout)
        os.close(self.stderr)

        self.outThread.join()
        self.errThread.join()
