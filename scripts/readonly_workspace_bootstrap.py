"""Suppress console windows for dependency commands without changing the bound runtime."""
import os
import subprocess
from scripts import readonly_workspace_runtime as runtime


class WindowlessCommands:
    def __getattr__(self, name):
        return getattr(subprocess, name)

    def run(self, *args, **kwargs):
        if os.name == 'nt':
            kwargs['creationflags'] = kwargs.get('creationflags', 0) | subprocess.CREATE_NO_WINDOW
        return subprocess.run(*args, **kwargs)


def main():
    # Replacement is confined to this bootstrap process and this module's
    # subprocess reference; shared subprocess functions remain unchanged.
    runtime.subprocess = WindowlessCommands()
    runtime.main()


if __name__ == '__main__':
    main()
