import contextlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap

PY3 = (sys.version_info >= (3,))
HG = 'hg'
_PYTHON_VERSIONS = [(3, 3)]
PYTHON_VERSIONS = []
for pyver in _PYTHON_VERSIONS:
    PYTHON_VERSIONS.append((pyver, 32))
    PYTHON_VERSIONS.append((pyver, 64))
SDK_ROOT = r"C:\Program Files\Microsoft SDKs\Windows"
BATCH_FAIL_ON_ERROR = "@IF %errorlevel% neq 0 exit /b %errorlevel%"

class Release(object):
    def __init__(self):
        root = os.path.dirname(__file__)
        self.root = os.path.realpath(root)

    @contextlib.contextmanager
    def _popen(self, args, **kw):
        env2 = kw.pop('env', {})
        env = dict(os.environ)
        # Force the POSIX locale
        env['LC_ALL'] = 'C'
        env.update(env2)
        print('+ ' + ' '.join(args))
        if PY3:
            kw['universal_newlines'] = True
        proc = subprocess.Popen(args, env=env, **kw)
        with proc:
            yield proc

    def get_output(self, *args, **kw):
        with self._popen(args, stdout=subprocess.PIPE, **kw) as proc:
            stdout, stderr = proc.communicate()
            return stdout

    def run_command(self, *args, **kw):
        with self._popen(args, **kw) as proc:
            exitcode = proc.wait()
        if exitcode:
            sys.exit(exitcode)

    def get_local_changes(self):
        status = self.get_output(HG, 'status')
        return [line for line in status.splitlines()
                if not line.startswith("?")]

    def remove_directory(self, name):
        path = os.path.join(self.root, name)
        if os.path.exists(path):
            print("Remove directory: %s" % name)
            shutil.rmtree(path)

    def remove_file(self, name):
        path = os.path.join(self.root, name)
        if os.path.exists(path):
            print("Remove file: %s" % name)
            os.unlink(path)

    def windows_sdk_setenv(self, pyver, bits):
        if pyver >= (3, 3):
            sdkver = "v7.1"
        else:
            sdkver = "v7.0"
        setenv = os.path.join(SDK_ROOT, sdkver, 'Bin', 'SetEnv.cmd')
        if not os.path.exists(setenv):
            print("Unable to find Windows SDK %s for Python %s.%s"
                  % (sdkver, pyver[0], pyver[1]))
            print("Please download and install it")
            print("%s does not exists" % setenv)
            sys.exit(1)
        if bits == 64:
            arch = '/x64'
        else:
            arch = '/x86'
        return ["CALL", setenv, "/release", arch]

    def get_python(self, version, bits):
        if bits == 32:
            python = 'c:\\Python%s%s_32bit\\python.exe' % version
        else:
            python = 'c:\\Python%s%s\\python.exe' % version
        if not os.path.exists(python):
            print("Unable to find python%s.%s" % version)
            print("%s does not exists" % python)
            sys.exit(1)
        code = (
            'import platform, sys; '
            'print("{ver.major}.{ver.minor} {bits}".format('
            'ver=sys.version_info, '
            'bits=platform.architecture()[0]))'
        )
        stdout = self.get_output(python, '-c', code)
        stdout = stdout.rstrip()
        expected = "%s.%s %sbit" % (version[0], version[1], bits)
        if stdout != expected:
            print("Python version or architecture doesn't match")
            print("got %r, expected %r" % (stdout, expected))
            print(python)
            sys.exit(1)
        return python

    def quote(self, arg):
        if not re.search("[ '\"]", arg):
            return arg
        # FIXME: should we escape "?
        return '"%s"' % arg

    def quote_args(self, args):
        return ' '.join(self.quote(arg) for arg in args)

    def cleanup(self):
        self.remove_directory('build')
        self.remove_directory('dist')
        self.remove_file('_overlapped.pyd')
        self.remove_file(os.path.join('asyncio', '_overlapped.pyd'))

    def sdist_upload(self):
        self.cleanup()
        self.run_command(sys.executable, 'setup.py', 'sdist', 'upload')

    def runtests(self, pyver, bits):
        pythonstr = "%s.%s (%s bits)" % (pyver[0], pyver[1], bits)
        python = self.get_python(pyver, bits)
        args = python, 'runtests.py', '-r'

        print("Run tests in release mode with %s" % pythonstr)
        self.run_command(*args)

        print("Run tests in debug mode with %s" % pythonstr)
        self.run_command(*args, env={'PYTHONASYNCIODEBUG': 1})

    def wheel_command(self, pyver, bits, *cmds):
        self.cleanup()

        setenv = self.windows_sdk_setenv(pyver, bits)

        python = self.get_python(pyver, bits)

        cmd = [python, 'setup.py'] + list(cmds)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".bat", delete=False) as temp:
            print("CD %s" % self.quote(self.root), file=temp)
            print(self.quote_args(setenv), file=temp)
            print(BATCH_FAIL_ON_ERROR, file=temp)
            print("", file=temp)
            print("SET DISTUTILS_USE_SDK=1", file=temp)
            print("SET MSSDK=1", file=temp)
            print(self.quote_args(cmd), file=temp)
            print(BATCH_FAIL_ON_ERROR, file=temp)

        try:
            self.run_command(temp.name)
        finally:
            os.unlink(temp.name)

    def test_wheel(self, pyver, bits):
        self.wheel_command(pyver, bits, 'bdist_wheel')

    def publish_wheel(self, pyver, bits):
        self.wheel_command(pyver, bits, 'bdist_wheel', 'upload')

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
        self.run_command(HG, 'up', hg_tag)

        # FIXME: enable running tests
        # On Windows, installing Python with the MSI doesn't install the test module,
        # so asyncio tests cannot run because test.script_helper is not found.
        #for pyver in PYTHON_VERSIONS:
        #    self.runtests(pyver, 32)
        #    self.runtests(pyver, 64)

        for pyver, bits in PYTHON_VERSIONS:
            self.test_wheel(pyver, bits)

        self.run_command(sys.executable, 'setup.py', 'register')

        self.sdist_upload()

        for pyver, bits in PYTHON_VERSIONS:
            self.publish_wheel(pyver, bits)

        print("")
        print("Publish version %s" % hg_tag)
        print("Uploaded:")
        print("- sdist")
        for pyver, bits in PYTHON_VERSIONS:
            print("- Windows wheel %s bits package for Python %s.%s"
                  % (bits, pyver[0], pyver[1]))

if __name__ == "__main__":
    Release().main()
