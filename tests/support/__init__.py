"""Helpers the test modules share, kept here so no test module imports another:

- sandbox: a private home for the test process (every test module imports it)
- fakes: stand-ins for the speech models, the worker's events, GLib's timers, GStreamer and
  external programs
- app_loader: the application's methods compiled from their source without GTK, for the
  application tests that need only NumPy
- gtk: PyGObject with GTK 4 and libadwaita and the application module, or the reason to skip
- windows: a GTK display for the tests that build windows, and the one application they share
"""
