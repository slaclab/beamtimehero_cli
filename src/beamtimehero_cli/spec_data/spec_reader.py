"""
spec_reader.py

Thin wrapper around silx.io.spech5.SpecH5 for reading SPEC data files
from BL15-2 at SSRL. Provides convenient functions for extracting scan
data, motor positions, and column arrays.

All functions accept a filepath and handle opening/closing the SpecH5
object internally, except open_spec_file() which returns the object
for the caller to manage.
"""
from __future__ import annotations

import os
import time
import logging
from pathlib import Path

import numpy as np
from silx.io.spech5 import SpecH5

from beamtimehero_cli import station

logger = logging.getLogger(__name__)

# Default y-column preferences (first match wins). Stations can override
# via BTH_DEFAULT_Y_COLUMNS (comma-separated); the default stays BL15-2.
DEFAULT_Y_COLUMNS = [
    c.strip()
    for c in (os.environ.get("BTH_DEFAULT_Y_COLUMNS")
               or station.default("BTH_DEFAULT_Y_COLUMNS", "vortDT,I0,I1")).split(",")
    if c.strip()
] or ["vortDT", "I0", "I1"]


def open_spec_file(filepath: str) -> SpecH5:
    """Open a SPEC file and return a SpecH5 object (HDF5-like interface).

    The caller is responsible for closing the object when done.
    Can be used as a context manager or closed explicitly.

    Args:
        filepath: Path to the SPEC data file.

    Returns:
        SpecH5 object providing HDF5-like access to scan data.

    Raises:
        FileNotFoundError: If the file does not exist.
        IOError: If the file cannot be parsed as a SPEC file.
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"SPEC file not found: {filepath}")
    return SpecH5(str(path))


def _find_scan_key(spec: SpecH5, scan_number: int) -> str:
    """Find the scan key for a given scan number.

    SpecH5 keys are formatted as 'N.O' where N is the scan number and
    O is the order (for repeated scan numbers). Returns the last
    occurrence (highest order) for the given scan number.

    Raises:
        KeyError: If no scan with the given number exists.
    """
    matching = [
        k for k in spec.keys()
        if int(k.split(".")[0]) == scan_number
    ]
    if not matching:
        available = sorted(set(int(k.split(".")[0]) for k in spec.keys()))
        raise KeyError(
            f"Scan #{scan_number} not found. "
            f"Available scans: {available}"
        )
    # Return the highest order (last repetition)
    return sorted(matching, key=lambda k: int(k.split(".")[1]))[-1]


def list_scans(filepath: str) -> list[dict]:
    """List all scans in a SPEC file.

    Args:
        filepath: Path to the SPEC data file.

    Returns:
        List of dicts, each containing:
            - scan_number (int): The scan number.
            - command (str): The SPEC command string (e.g., 'ascan m1vert -1 1 30 0.2').
            - date (str): Timestamp from the scan header.
            - n_points (int): Number of data points in the scan.
    """
    spec = open_spec_file(filepath)
    try:
        scans = []
        for key in sorted(spec.keys(), key=lambda k: (int(k.split(".")[0]), int(k.split(".")[1]))):
            scan = spec[key]
            scan_num = int(key.split(".")[0])

            command = scan["title"][()].decode() if isinstance(scan["title"][()], bytes) else str(scan["title"][()])

            date = ""
            try:
                date_val = scan["start_time"][()]
                date = date_val.decode() if isinstance(date_val, bytes) else str(date_val)
            except Exception:
                pass

            # Count data points from the first measurement column
            n_points = 0
            try:
                meas = scan["measurement"]
                first_col = list(meas.keys())[0]
                n_points = len(np.array(meas[first_col]))
            except Exception:
                pass

            scans.append({
                "scan_number": scan_num,
                "command": command,
                "date": date,
                "n_points": n_points,
            })
        return scans
    finally:
        spec.close()


def get_scan_data(filepath: str, scan_number: int) -> dict:
    """Get full data for a specific scan.

    Args:
        filepath: Path to the SPEC data file.
        scan_number: The scan number to retrieve.

    Returns:
        Dict containing:
            - scan_number (int)
            - command (str): The SPEC command string.
            - columns (list[str]): Column names from the #L line.
            - data (dict[str, np.ndarray]): Mapping of column name to values.
            - motor_positions (dict[str, float]): All motor positions from #P lines.
            - n_points (int): Number of data points.
            - scanned_motor (str): Motor name extracted from the command.

    Raises:
        KeyError: If the scan number is not found.
    """
    spec = open_spec_file(filepath)
    try:
        key = _find_scan_key(spec, scan_number)
        scan = spec[key]

        command = scan["title"][()].decode() if isinstance(scan["title"][()], bytes) else str(scan["title"][()])

        # Extract column data from measurement group
        meas = scan["measurement"]
        columns = list(meas.keys())
        data = {}
        n_points = 0
        for col in columns:
            arr = np.array(meas[col])
            if arr.ndim == 1:
                data[col] = arr
                n_points = max(n_points, len(arr))

        # Extract motor positions from instrument/positioners
        motor_positions = _extract_motor_positions(scan)

        # Parse the scanned motor from the command
        parsed = parse_scan_command(command)
        scanned_motor = parsed.get("motor", "")

        return {
            "scan_number": scan_number,
            "command": command,
            "columns": columns,
            "data": data,
            "motor_positions": motor_positions,
            "n_points": n_points,
            "scanned_motor": scanned_motor,
        }
    finally:
        spec.close()


def get_scan_column(filepath: str, scan_number: int, column_name: str) -> np.ndarray:
    """Get a single column of data from a scan.

    Args:
        filepath: Path to the SPEC data file.
        scan_number: The scan number to retrieve.
        column_name: Name of the column (must match a label from the #L line).

    Returns:
        1D numpy array of values for the requested column.

    Raises:
        KeyError: If the scan or column is not found.
    """
    spec = open_spec_file(filepath)
    try:
        key = _find_scan_key(spec, scan_number)
        scan = spec[key]
        meas = scan["measurement"]

        if column_name not in meas:
            available = list(meas.keys())
            raise KeyError(
                f"Column '{column_name}' not found in scan #{scan_number}. "
                f"Available columns: {available}"
            )

        return np.array(meas[column_name])
    finally:
        spec.close()


def get_motor_positions(filepath: str, scan_number: int) -> dict[str, float]:
    """Get all motor positions for a scan (from #P lines).

    Args:
        filepath: Path to the SPEC data file.
        scan_number: The scan number to retrieve.

    Returns:
        Dict mapping motor name to position value.

    Raises:
        KeyError: If the scan number is not found.
    """
    spec = open_spec_file(filepath)
    try:
        key = _find_scan_key(spec, scan_number)
        scan = spec[key]
        return _extract_motor_positions(scan)
    finally:
        spec.close()


def _extract_motor_positions(scan) -> dict[str, float]:
    """Extract motor positions from a scan's instrument/positioners group.

    For motors that were scanned (array values), returns the value from
    the first data point (the starting position).
    """
    positions = {}
    try:
        pos = scan["instrument/positioners"]
        for motor_name in pos.keys():
            val = pos[motor_name][()]
            if isinstance(val, np.ndarray) and val.ndim > 0:
                # Scanned motor -- use the first value as the "position"
                positions[motor_name] = float(val[0]) if len(val) > 0 else np.nan
            else:
                positions[motor_name] = float(val)
    except Exception:
        logger.warning("Could not extract motor positions from scan")
    return positions


def get_scan_command(filepath: str, scan_number: int) -> str:
    """Get the SPEC command string for a scan (from #S line).

    Args:
        filepath: Path to the SPEC data file.
        scan_number: The scan number to retrieve.

    Returns:
        The command string, e.g., 'ascan m1vert -1 1 30 0.2'.

    Raises:
        KeyError: If the scan number is not found.
    """
    spec = open_spec_file(filepath)
    try:
        key = _find_scan_key(spec, scan_number)
        title = spec[key]["title"][()]
        return title.decode() if isinstance(title, bytes) else str(title)
    finally:
        spec.close()


def parse_scan_command(command: str) -> dict:
    """Parse a SPEC scan command string into its component parts.

    Supported scan types:
        - ascan/dscan: motor start end npoints time
        - a2scan/d2scan: motor1 start1 end1 motor2 start2 end2 npoints time
        - cscan/cdscan: motor center halfwidth npoints time
        - mesh: motor1 start1 end1 npts1 motor2 start2 end2 npts2 time
        - gscan: motor start end1 step1 [end2 step2 ...] time
        - timescan: npoints time

    Args:
        command: The SPEC command string (e.g., 'dscan m1vert -1.7 1.7 90 0.2').

    Returns:
        Dict with parsed components. Always includes 'scan_type'.
        For motor scans, includes 'motor'. Numeric fields are floats/ints
        as appropriate. Returns partial results for unrecognized formats.
    """
    parts = command.strip().split()
    if not parts:
        return {"scan_type": "unknown"}

    scan_type = parts[0]
    result = {"scan_type": scan_type}

    try:
        if scan_type in ("ascan", "dscan"):
            # ascan motor start end npoints time
            if len(parts) >= 6:
                result["motor"] = parts[1]
                result["start"] = float(parts[2])
                result["end"] = float(parts[3])
                result["npoints"] = int(float(parts[4]))
                result["time"] = float(parts[5])

        elif scan_type in ("a2scan", "d2scan"):
            # a2scan motor1 start1 end1 motor2 start2 end2 npoints time
            if len(parts) >= 9:
                result["motor"] = parts[1]
                result["start"] = float(parts[2])
                result["end"] = float(parts[3])
                result["motor2"] = parts[4]
                result["start2"] = float(parts[5])
                result["end2"] = float(parts[6])
                result["npoints"] = int(float(parts[7]))
                result["time"] = float(parts[8])

        elif scan_type in ("cscan", "cdscan"):
            # cscan motor center halfwidth npoints time
            if len(parts) >= 5:
                result["motor"] = parts[1]
                center = float(parts[2])
                halfwidth = float(parts[3])
                result["center"] = center
                result["halfwidth"] = halfwidth
                result["start"] = center - halfwidth
                result["end"] = center + halfwidth
                if len(parts) >= 6:
                    result["npoints"] = int(float(parts[4]))
                    result["time"] = float(parts[5])

        elif scan_type == "mesh":
            # mesh motor1 start1 end1 npts1 motor2 start2 end2 npts2 time
            if len(parts) >= 10:
                result["motor"] = parts[1]
                result["start"] = float(parts[2])
                result["end"] = float(parts[3])
                result["npoints"] = int(float(parts[4]))
                result["motor2"] = parts[5]
                result["start2"] = float(parts[6])
                result["end2"] = float(parts[7])
                result["npoints2"] = int(float(parts[8]))
                result["time"] = float(parts[9])

        elif scan_type == "gscan":
            # gscan motor start end1 step1 [end2 step2 ...] time
            if len(parts) >= 5:
                result["motor"] = parts[1]
                result["start"] = float(parts[2])
                # Last value is count time, pairs before that are end/step segments
                result["time"] = float(parts[-1])
                # Collect segments
                segments = []
                seg_parts = parts[3:-1]
                for i in range(0, len(seg_parts) - 1, 2):
                    segments.append({
                        "end": float(seg_parts[i]),
                        "step": float(seg_parts[i + 1]),
                    })
                result["segments"] = segments
                if segments:
                    result["end"] = segments[-1]["end"]

        elif scan_type == "timescan":
            # timescan npoints time
            if len(parts) >= 3:
                result["npoints"] = int(float(parts[1]))
                result["time"] = float(parts[2])

        else:
            # Unknown scan type -- try to extract motor name as a fallback
            if len(parts) >= 2 and not _is_number(parts[1]):
                result["motor"] = parts[1]

    except (ValueError, IndexError) as exc:
        logger.debug("Could not fully parse scan command '%s': %s", command, exc)

    return result


def _is_number(s: str) -> bool:
    """Check if a string can be parsed as a float."""
    try:
        float(s)
        return True
    except ValueError:
        return False


def get_scan_xy(
    filepath: str,
    scan_number: int,
    x_col: str | None = None,
    y_col: str | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Convenience function to get x and y arrays for a scan.

    If x_col is None, uses the scanned motor column (parsed from the
    scan command). If y_col is None, uses the first available column
    from the default list: vortDT, I0, I1.

    Args:
        filepath: Path to the SPEC data file.
        scan_number: The scan number to retrieve.
        x_col: Column name for x values, or None for auto-detection.
        y_col: Column name for y values, or None for auto-detection.

    Returns:
        Tuple of (x_array, y_array) as numpy arrays.

    Raises:
        KeyError: If the scan, or required columns, are not found.
        ValueError: If no suitable default column can be found.
    """
    spec = open_spec_file(filepath)
    try:
        key = _find_scan_key(spec, scan_number)
        scan = spec[key]
        meas = scan["measurement"]
        available = list(meas.keys())

        # Resolve x column
        if x_col is None:
            title = scan["title"][()]
            command = title.decode() if isinstance(title, bytes) else str(title)
            parsed = parse_scan_command(command)
            x_col = parsed.get("motor")
            if x_col is None or x_col not in available:
                # Fall back to first column
                x_col = available[0] if available else None
            if x_col is None:
                raise ValueError(f"No columns available in scan #{scan_number}")

        # Resolve y column
        if y_col is None:
            for candidate in DEFAULT_Y_COLUMNS:
                if candidate in available:
                    y_col = candidate
                    break
            if y_col is None:
                # Use the last column as fallback (often a detector)
                y_col = available[-1] if available else None
            if y_col is None:
                raise ValueError(f"No columns available in scan #{scan_number}")

        if x_col not in available:
            raise KeyError(
                f"X column '{x_col}' not found in scan #{scan_number}. "
                f"Available: {available}"
            )
        if y_col not in available:
            raise KeyError(
                f"Y column '{y_col}' not found in scan #{scan_number}. "
                f"Available: {available}"
            )

        x = np.array(meas[x_col])
        y = np.array(meas[y_col])
        return x, y
    finally:
        spec.close()


def wait_for_scan(
    filepath: str,
    scan_number: int,
    timeout: float = 2.0,
    poll_interval: float = 0.2,
) -> bool:
    """Wait for a scan to appear in a SPEC file.

    SPEC writes data incrementally, so a scan may not be immediately
    available after it completes. This function polls the file until
    the scan appears or the timeout is reached.

    The file is reopened on each poll to pick up new data written by SPEC.

    Args:
        filepath: Path to the SPEC data file.
        scan_number: The scan number to wait for.
        timeout: Maximum time to wait in seconds (default 2.0).
        poll_interval: Time between polls in seconds (default 0.2).

    Returns:
        True if the scan was found within the timeout, False otherwise.
    """
    deadline = time.time() + timeout

    while time.time() < deadline:
        try:
            spec = SpecH5(filepath)
            try:
                _find_scan_key(spec, scan_number)
                return True
            except KeyError:
                pass
            finally:
                spec.close()
        except Exception:
            # File may not exist yet or be in the middle of a write
            pass

        remaining = deadline - time.time()
        if remaining > 0:
            time.sleep(min(poll_interval, remaining))

    return False
