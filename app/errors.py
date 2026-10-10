"""errors.py - the app-wide friendly-error class.

ProjectError is what every layer raises when something should reach
the user as a plain-words Problem message instead of a traceback. It
lives here, in a module with no slicer in it, so every backend raises
the same class and the front end catches exactly one thing.

(Historically it was born in slicer_project.py, which still re-exports
it - `from slicer_project import ProjectError` keeps working.)
"""


class ProjectError(Exception):
    """Raised for anything the UI should show as a friendly error."""
