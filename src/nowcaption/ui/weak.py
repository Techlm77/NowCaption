"""Signal handlers GTK can hold without keeping their window alive. No widgets of its own."""
import weakref


def weakly(method):
    """method, as a handler GTK can hold without keeping method's object alive. Python's garbage
    collector cannot see GTK's references, so a cycle through one, such as a window's button that
    calls the window, would keep a closed window forever."""
    ref = weakref.WeakMethod(method)

    def call(*args):
        live = ref()
        return live(*args) if live else None
    return call
