"""Giving memory the C allocator has freed back to the system, once a minute while captioning.
Depends on nothing else in NowCaption.
"""
import ctypes


def release_free_memory():
    """Give memory the C allocator has freed back to the system (glibc's malloc_trim).

    After a long session glibc holds about 45 MB of freed memory that the models and
    GTK no longer use. Trimming once a minute keeps NowCaption about 16% smaller and
    takes well under a millisecond. Returns False where it is not available.
    """
    global _malloc_trim
    if _malloc_trim is None:
        try:
            _malloc_trim = ctypes.CDLL('libc.so.6').malloc_trim
        except (OSError, AttributeError):  # not glibc
            _malloc_trim = False
    if not _malloc_trim:
        return False
    _malloc_trim(0)
    return True


_malloc_trim = None
