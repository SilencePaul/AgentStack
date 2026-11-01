"""Own the Qoder process tree, including descendants of an exited launcher."""
import os
import signal


class ProcessTree:
    def __init__(self):
        self.process = None
        self.job = None
        if os.name == 'nt':
            import ctypes as c
            from ctypes import wintypes as w
            class Limits(c.Structure):
                _fields_ = [('process_time', c.c_longlong), ('job_time', c.c_longlong),
                            ('flags', w.DWORD), ('min_ws', c.c_size_t), ('max_ws', c.c_size_t),
                            ('active', w.DWORD), ('affinity', c.c_size_t),
                            ('priority', w.DWORD), ('scheduling', w.DWORD)]
            class Counters(c.Structure):
                _fields_ = [(name, c.c_ulonglong) for name in
                            ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]
            class Extended(c.Structure):
                _fields_ = [('basic', Limits), ('io', Counters),
                            ('process_mem', c.c_size_t), ('job_mem', c.c_size_t),
                            ('peak_process', c.c_size_t), ('peak_job', c.c_size_t)]
            kernel = c.WinDLL('kernel32', use_last_error=True)
            kernel.CreateJobObjectW.argtypes = [c.c_void_p, w.LPCWSTR]
            kernel.CreateJobObjectW.restype = w.HANDLE
            kernel.SetInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]
            kernel.SetInformationJobObject.restype = w.BOOL
            kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
            kernel.AssignProcessToJobObject.restype = w.BOOL
            kernel.TerminateJobObject.argtypes = [w.HANDLE, w.UINT]
            kernel.TerminateJobObject.restype = w.BOOL
            kernel.CloseHandle.argtypes = [w.HANDLE]
            kernel.CloseHandle.restype = w.BOOL
            self.kernel = kernel
            self.job = kernel.CreateJobObjectW(None, None)
            if not self.job:
                raise c.WinError(c.get_last_error())
            limits = Extended()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not kernel.SetInformationJobObject(self.job, 9, c.byref(limits), c.sizeof(limits)):
                error = c.WinError(c.get_last_error())
                kernel.CloseHandle(self.job); self.job = None
                raise error

    def attach(self, process):
        self.process = process
        if self.job:
            import ctypes
            if not self.kernel.AssignProcessToJobObject(self.job, int(process._handle)):
                error = ctypes.WinError(ctypes.get_last_error())
                process.kill(); process.wait()
                raise error

    def close(self):
        if self.job:
            self.kernel.TerminateJobObject(self.job, 1)
            self.kernel.CloseHandle(self.job)
            self.job = None
        elif self.process and os.name != 'nt':
            # Do not check parent.poll(): the leader may have exited before its children.
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if self.process:
            self.process.wait(timeout=10)
            self.process = None
