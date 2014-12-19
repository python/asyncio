"""
Script to upload 32 bits and 64 bits wheel packages for Python 3.3 on Windows.

Usage: "python release.py HG_TAG" where HG_TAG is a Mercurial tag, usually
a version number like "3.4.2".

Modify manually the dry_run attribute to upload files.

It requires the Windows SDK 7.1 on Windows 64 bits and the aiotest module.
"""
import contextlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap

PROJECT = 'asyncio'
DEBUG_ENV_VAR = 'PYTHONASYNCIODEBUG'
PYTHON_VERSIONS = (
    ((3, 3), 32),
    ((3, 3), 64),
)
PY3 = (sys.version_info >= (3,))
HG = 'hg'
SDK_ROOT = r"C:\Program Files\Microsoft SDKs\Windows"
BATCH_FAIL_ON_ERROR = "@IF %errorlevel% neq 0 exit /b %errorlevel%"


class PythonVersion:
    def __init__(self, major, minor, bits):
        self.major = major
        self.minor = minor
        self.bits = bits
        self._executable = None

    def get_executable(self, app):
        if self._executable:
            return self._executable

        if self.bits == 32:
            python = 'c:\\Python%s%s_32bit\\python.exe' % (self.major, self.minor)
        else:
            python = 'c:\\Python%s%s\\python.exe' % (self.major, self.minor)
        if not os.path.exists(python):
            print("Unable to find python %s" % self)
            print("%s does not exists" % python)
            sys.exit(1)
        code = (
            'import platform, sys; '
            'print("{ver.major}.{ver.minor} {bits}".format('
            'ver=sys.version_info, '
            'bits=platform.architecture()[0]))'
        )
        exitcode, stdout = app.get_output(python, '-c', code)
        stdout = stdout.rstrip()
        expected = "%s.%s %sbit" % (self.major, self.minor, self.bits)
        if stdout != expected:
            print("Python version or architecture doesn't match")
            print("got %r, expected %r" % (stdout, expected))
            print(python)
            sys.exit(1)
        self._executable = python
        return python

    def __str__(self):
        return 'Python %s.%s (%s bits)' % (self.major, self.minor, self.bits)


class Release(object):
    def __init__(self):
        root = os.path.dirname(__file__)
        self.root = os.path.realpath(root)
        # Set these attributes to True to run also register sdist upload
        self.register = False
        self.sdist = False
        self.dry_run = True
        self.test = True
        self.aiotest = False
        self.verbose = False
        self.python_versions = [
            PythonVersion(pyver[0], pyver[1], bits)
            for pyver, bits in PYTHON_VERSIONS]

    @contextlib.contextmanager
    def _popen(self, args, **kw):
        verbose = kw.pop('verbose', True)
        if self.verbose and verbose:
            print('+ ' + ' '.join(args))
        if PY3:
            kw['universal_newlines'] = True
        proc = subprocess.Popen(args, **kw)
        with proc:
            yield proc

    def get_output(self, *args, **kw):
        kw['stdout'] = subprocess.PIPE
        kw['stderr'] = subprocess.STDOUT
        with self._popen(args, **kw) as proc:
            stdout, stderr = proc.communicate()
            return proc.returncode, stdout

    def run_command(self, *args, **kw):
        with self._popen(args, **kw) as proc:
            exitcode = proc.wait()
        if exitcode:
            sys.exit(exitcode)

    def get_local_changes(self):
        exitcode, status = self.get_output(HG, 'status')
        return [line for line in status.splitlines()
                if not line.startswith("?")]

    def remove_directory(self, name):
        path = os.path.join(self.root, name)
        if os.path.exists(path):
            if self.verbose:
                print("Remove directory: %s" % name)
            shutil.rmtree(path)

    def remove_file(self, name):
        path = os.path.join(self.root, name)
        if os.path.exists(path):
            if self.verbose:
                print("Remove file: %s" % name)
            os.unlink(path)

    def windows_sdk_setenv(self, pyver):
        if (pyver.major, pyver.minor) >= (3, 3):
            path = "v7.1"
            sdkver = (7, 1)
        else:
            path = "v7.0"
            sdkver = (7, 0)
        setenv = os.path.join(SDK_ROOT, path, 'Bin', 'SetEnv.cmd')
        if not os.path.exists(setenv):
            print("Unable to find Windows SDK %s.%s for %s"
                  % (sdkver[0], sdkver[1], pyver))
            print("Please download and install it")
            print("%s does not exists" % setenv)
            sys.exit(1)
        if pyver.bits == 64:
            arch = '/x64'
        else:
            arch = '/x86'
        cmd = ["CALL", setenv, "/release", arch]
        return (cmd, sdkver)

    def quote(self, arg):
        if not re.search("[ '\"]", arg):
            return arg
        # FIXME: should we escape "?
        return '"%s"' % arg

    def quote_args(self, args):
        return ' '.join(self.quote(arg) for arg in args)

    def cleanup(self):
        if self.verbose:
            print("Cleanup")
        self.remove_directory('build')
        self.remove_directory('dist')
        self.remove_file('_overlapped.pyd')
        self.remove_file(os.path.join(PROJECT, '_overlapped.pyd'))

    def sdist_upload(self):
        self.cleanup()
        self.run_command(sys.executable, 'setup.py', 'sdist', 'upload')

    def runtests(self, pyver):
        print("Run tests on %s" % pyver)

        python = pyver.get_executable(self)

        print("Build _overlapped.pyd for %s" % pyver)
        self.build(pyver, 'build')
        if pyver.bits == 64:
            arch = 'win-amd64'
        else:
            arch = 'win32'
        build_dir = 'lib.%s-%s.%s' % (arch, pyver.major, pyver.minor)
        src = os.path.join(self.root, 'build', build_dir, PROJECT, '_overlapped.pyd')
        dst = os.path.join(self.root, PROJECT, '_overlapped.pyd')
        shutil.copyfile(src, dst)

        release_env = dict(os.environ)
        release_env.pop(DEBUG_ENV_VAR, None)

        dbg_env = dict(os.environ)
        dbg_env[DEBUG_ENV_VAR] = '1'

        args = (python, 'runtests.py', '-r')
        print("Run runtests.py in release mode on %s" % pyver)
        self.run_command(*args, env=release_env)

        print("Run runtests.py in debug mode on %s" % pyver)
        self.run_command(*args, env=dbg_env)

        if self.aiotest:
            args = (python, 'run_aiotest.py')
            print("Run aiotest in release mode on %s" % pyver)
            self.run_command(*args, env=release_env)

            print("Run aiotest in debug mode on %s" % pyver)
            self.run_command(*args, env=dbg_env)
        print("")

    def build(self, pyver, *cmds):
        self.cleanup()

        setenv, sdkver = self.windows_sdk_setenv(pyver)

        python = pyver.get_executable(self)

        cmd = [python, 'setup.py'] + list(cmds)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".bat", delete=False) as temp:
            print("SETLOCAL EnableDelayedExpansion", file=temp)
            print(self.quote_args(setenv), file=temp)
            print(BATCH_FAIL_ON_ERROR, file=temp)
            # Restore console colors: lightgrey on black
            print("COLOR 07", file=temp)
            print("", file=temp)
            print("SET DISTUTILS_USE_SDK=1", file=temp)
            print("SET MSSDK=1", file=temp)
            print("CD %s" % self.quote(self.root), file=temp)
            print(self.quote_args(cmd), file=temp)
            print(BATCH_FAIL_ON_ERROR, file=temp)

        try:
            if self.verbose:
                print("Setup Windows SDK")
                print("+ " + ' '.join(cmd))
            # SDK 7.1 uses the COLOR command which makes SetEnv.cmd failing
            # if the stdout is not a TTY (if we redirect stdout into a file)
            if self.verbose or sdkver >= (7, 1):
                self.run_command(temp.name, verbose=False)
            else:
                exitcode, stdout = self.get_output(temp.name, verbose=False)
                if exitcode:
                    sys.stdout.write(stdout)
                    sys.stdout.flush()
        finally:
            os.unlink(temp.name)

    def test_wheel(self, pyver):
        print("Test building wheel package for %s" % pyver)
        self.build(pyver, 'bdist_wheel')

    def publish_wheel(self, pyver):
        self.build(pyver, 'bdist_wheel', 'upload')

    def main(self):
        try:
            pos = sys.argv[1:].index('--ignore')
        except ValueError:
            ignore = False
        else:
            ignore = True
            del sys.argv[1+pos]
        if len(sys.argv) != 2:
            print("usage: %s hg_tag" % sys.argv[0])
            sys.exit(1)

        print("Directory: %s" % self.root)
        os.chdir(self.root)

        if not ignore:
            lines = self.get_local_changes()
        else:
            lines = ()
        if lines:
            print("ERROR: Found local changes")
            for line in lines:
                print(line)
            print("")
            print("Revert local changes")
            print("or use the --ignore command line option")
            sys.exit(1)

        hg_tag = sys.argv[1]
        print("Update repository to revision %s" % hg_tag)
        exitcode, output = self.get_output(HG, 'update', hg_tag)
        if exitcode:
            sys.stdout.write(output)
            sys.stdout.flush()
            sys.exit(exitcode)

        if self.test:
            for pyver in self.python_versions:
                self.runtests(pyver)

        for pyver in self.python_versions:
            self.test_wheel(pyver)

        if self.dry_run:
            sys.exit(0)

        if self.register:
            self.run_command(sys.executable, 'setup.py', 'register')

        if self.sdist:
            self.sdist_upload()

        for pyver in self.python_versions:
            self.publish_wheel(pyver)

        print("")
        if self.register:
            print("Publish version %s" % hg_tag)
        print("Uploaded:")
        if self.sdist:
            print("- sdist")
        for pyver in self.python_versions:
            print("- Windows wheel package for %s" % pyver)

if __name__ == "__main__":
    Release().main()
