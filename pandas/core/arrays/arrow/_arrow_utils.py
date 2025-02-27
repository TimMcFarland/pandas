from __future__ import annotations

import json
import warnings

import numpy as np
import pyarrow

from pandas._libs import lib
from pandas._libs.interval import _warning_interval
from pandas.errors import PerformanceWarning
from pandas.util._exceptions import find_stack_level

from pandas.core.arrays.interval import VALID_CLOSED


def fallback_performancewarning(version: str | None = None):
    """
    Raise a PerformanceWarning for falling back to ExtensionArray's
    non-pyarrow method
    """
    msg = "Falling back on a non-pyarrow code path which may decrease performance."
    if version is not None:
        msg += f" Upgrade to pyarrow >={version} to possibly suppress this warning."
    warnings.warn(msg, PerformanceWarning, stacklevel=find_stack_level())


def pyarrow_array_to_numpy_and_mask(arr, dtype: np.dtype):
    """
    Convert a primitive pyarrow.Array to a numpy array and boolean mask based
    on the buffers of the Array.

    At the moment pyarrow.BooleanArray is not supported.

    Parameters
    ----------
    arr : pyarrow.Array
    dtype : numpy.dtype

    Returns
    -------
    (data, mask)
        Tuple of two numpy arrays with the raw data (with specified dtype) and
        a boolean mask (validity mask, so False means missing)
    """
    dtype = np.dtype(dtype)

    buflist = arr.buffers()
    # Since Arrow buffers might contain padding and the data might be offset,
    # the buffer gets sliced here before handing it to numpy.
    # See also https://github.com/pandas-dev/pandas/issues/40896
    offset = arr.offset * dtype.itemsize
    length = len(arr) * dtype.itemsize
    data_buf = buflist[1][offset : offset + length]
    data = np.frombuffer(data_buf, dtype=dtype)
    bitmask = buflist[0]
    if bitmask is not None:
        mask = pyarrow.BooleanArray.from_buffers(
            pyarrow.bool_(), len(arr), [None, bitmask], offset=arr.offset
        )
        mask = np.asarray(mask)
    else:
        mask = np.ones(len(arr), dtype=bool)
    return data, mask


class ArrowPeriodType(pyarrow.ExtensionType):
    def __init__(self, freq) -> None:
        # attributes need to be set first before calling
        # super init (as that calls serialize)
        self._freq = freq
        pyarrow.ExtensionType.__init__(self, pyarrow.int64(), "pandas.period")

    @property
    def freq(self):
        return self._freq

    def __arrow_ext_serialize__(self):
        metadata = {"freq": self.freq}
        return json.dumps(metadata).encode()

    @classmethod
    def __arrow_ext_deserialize__(cls, storage_type, serialized):
        metadata = json.loads(serialized.decode())
        return ArrowPeriodType(metadata["freq"])

    def __eq__(self, other):
        if isinstance(other, pyarrow.BaseExtensionType):
            return type(self) == type(other) and self.freq == other.freq
        else:
            return NotImplemented

    def __hash__(self):
        return hash((str(self), self.freq))

    def to_pandas_dtype(self):
        import pandas as pd

        return pd.PeriodDtype(freq=self.freq)


# register the type with a dummy instance
_period_type = ArrowPeriodType("D")
pyarrow.register_extension_type(_period_type)


class ArrowIntervalType(pyarrow.ExtensionType):
    def __init__(
        self,
        subtype,
        inclusive: str | None = None,
        closed: None | lib.NoDefault = lib.no_default,
    ) -> None:
        # attributes need to be set first before calling
        # super init (as that calls serialize)
        inclusive, closed = _warning_interval(inclusive, closed)
        assert inclusive in VALID_CLOSED
        self._closed = inclusive
        if not isinstance(subtype, pyarrow.DataType):
            subtype = pyarrow.type_for_alias(str(subtype))
        self._subtype = subtype

        storage_type = pyarrow.struct([("left", subtype), ("right", subtype)])
        pyarrow.ExtensionType.__init__(self, storage_type, "pandas.interval")

    @property
    def subtype(self):
        return self._subtype

    @property
    def inclusive(self):
        return self._closed

    def __arrow_ext_serialize__(self):
        metadata = {"subtype": str(self.subtype), "inclusive": self.inclusive}
        return json.dumps(metadata).encode()

    @classmethod
    def __arrow_ext_deserialize__(cls, storage_type, serialized):
        metadata = json.loads(serialized.decode())
        subtype = pyarrow.type_for_alias(metadata["subtype"])
        inclusive = metadata["inclusive"]
        return ArrowIntervalType(subtype, inclusive)

    def __eq__(self, other):
        if isinstance(other, pyarrow.BaseExtensionType):
            return (
                type(self) == type(other)
                and self.subtype == other.subtype
                and self.inclusive == other.inclusive
            )
        else:
            return NotImplemented

    def __hash__(self):
        return hash((str(self), str(self.subtype), self.inclusive))

    def to_pandas_dtype(self):
        import pandas as pd

        return pd.IntervalDtype(self.subtype.to_pandas_dtype(), self.inclusive)


# register the type with a dummy instance
_interval_type = ArrowIntervalType(pyarrow.int64(), "left")
pyarrow.register_extension_type(_interval_type)
