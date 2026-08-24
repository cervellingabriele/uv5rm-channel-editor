"""UV-5RM Plus GPS - Fast Channel Editor.

A small, dependency-free editor for Baofeng ``.xlc`` codeplug files
(UV-5RM Plus / 5RH Pro). See :mod:`uv5rm_editor.xlc_format` for the file-format
engine and :mod:`uv5rm_editor.editor` for the Tkinter GUI.
"""

from .xlc_format import Channel, Codeplug, CodeplugError

__version__ = "1.0.0"
__all__ = ["Channel", "Codeplug", "CodeplugError", "__version__"]
