"""Lazy native-home shared/exclusive OS gate; never a session catalog."""
from __future__ import annotations
import os
from pathlib import Path


class MaintenanceBusy(ValueError):
    data={'reason':'native-maintenance-busy','executed':False,'replayed':False}


def native_home(config=None):
    return Path((config or {}).get('home') or os.environ.get('AMPLIFIER_HOME') or Path.home()/'.amplifier').expanduser().resolve()


def checked(path):
    path=Path(path).absolute()
    if any(p.is_symlink() for p in (path,*path.parents)):
        raise ValueError('Native maintenance paths cannot traverse symbolic links')
    return path


class Gate:
    """Nonblocking flock. Shared holders are whole workers or bounded requests.

    The descriptor is never inherited. Process death releases it; no stale file
    deletion or guessed PID liveness is needed. POSIX is required, as it is for
    Foundation's selected-session writer ownership.
    """
    def __init__(self,config=None,*,exclusive=False):
        try:import fcntl
        except ImportError:raise ValueError('Native maintenance writer fencing requires POSIX flock') from None
        directory=checked(native_home(config));directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        path=checked(directory/'.amplifier-acp-maintenance.lock')
        fd=os.open(path,os.O_RDWR|os.O_CREAT|getattr(os,'O_NOFOLLOW',0),0o600)
        try:
            os.set_inheritable(fd,False)
            fcntl.flock(fd,(fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)|fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise MaintenanceBusy('Native maintenance requires quiescent writers; no work was started or replayed.') from None
        except BaseException:
            os.close(fd);raise
        self.fd=fd

    def release(self):
        if self.fd is not None:
            import fcntl
            fd,self.fd=self.fd,None
            try:fcntl.flock(fd,fcntl.LOCK_UN)
            finally:os.close(fd)

    def __enter__(self):return self
    def __exit__(self,*_):self.release()
