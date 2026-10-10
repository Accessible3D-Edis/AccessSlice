"""backends - one SlicerBackend per supported slicer, found by id.

The ids are config_store.SLICERS ids; get_backend() is how the rest
of the app reaches everything slicer-specific. See backends.base for
the contract and docs\\phase3-backend-refactor.md for the plan.
"""

from backends.base import BackendError, SlicerBackend  # noqa: F401
from backends.bambu import BambuBackend
from backends.creality import CrealityBackend
from backends.prusa import PrusaBackend
from backends.orca import OrcaBackend
from backends.anycubic import AnycubicBackend

# One shared instance per backend - they hold no per-project state.
_BACKENDS = {b.id: b for b in (BambuBackend(), CrealityBackend(),
                               PrusaBackend(), OrcaBackend(),
                               AnycubicBackend())}


def get_backend(slicer_id):
    """The backend for a slicer id.

    Raises BackendError in plain words for ids that exist in the
    picker but are not built yet, and for ids never heard of - both
    can only reach here through a hand-edited config file.
    """
    backend = _BACKENDS.get(slicer_id)
    if backend is None:
        names = " and ".join(_BACKENDS[i].display_name
                             for i in sorted(_BACKENDS))
        raise BackendError(
            f"AccessSlice has no support for the slicer '{slicer_id}' "
            f"yet. Only {names} are supported so far - pick one on "
            "the Settings page.")
    return backend


def supported_ids():
    """The slicer ids that have a working backend."""
    return sorted(_BACKENDS)
