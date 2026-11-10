"""Interval variability and externally measured hemodynamics (no PPG calibration)."""
import numpy as np


VARIABILITY_FIELDS = (
    'prv_rmssd_ms', 'prv_sdnn_ms', 'prv_sdsd_ms', 'prv_pnn20_pct',
    'prv_pnn50_pct', 'prv_cvnn', 'prv_cvsd', 'prv_lnrmssd',
)


def interval_variability(intervals_ms):
    """Time-domain statistics of consecutive, already reviewed intervals.

    SDNN/SDSD use sample standard deviation (ddof=1). pNN denominators are
    the number of successive interval pairs, with strict >20/>50 ms tests.
    Naming follows HRV conventions, but PPG inputs remain PRV, not ECG NN data.
    No removal, interpolation, or concatenation of intervals is performed.
    """
    rr = np.asarray(intervals_ms, dtype=float)
    if rr.ndim != 1 or rr.size < 3 or not np.isfinite(rr).all() or np.any(rr <= 0):
        raise ValueError('Need at least three finite positive consecutive intervals')
    differences = np.diff(rr)
    absolute_differences = np.abs(differences)
    # Sample-to-millisecond conversion can leave roundoff at exact thresholds.
    # A 1e-9 ms tolerance is far below any acquisition timing resolution.
    over_20 = (absolute_differences > 20) & ~np.isclose(absolute_differences, 20, rtol=0, atol=1e-9)
    over_50 = (absolute_differences > 50) & ~np.isclose(absolute_differences, 50, rtol=0, atol=1e-9)
    mean = float(np.mean(rr))
    rmssd = float(np.sqrt(np.mean(differences ** 2)))
    sdnn = float(np.std(rr, ddof=1))
    return {
        'prv_rmssd_ms': rmssd,
        'prv_sdnn_ms': sdnn,
        'prv_sdsd_ms': float(np.std(differences, ddof=1)),
        'prv_pnn20_pct': float(100 * np.mean(over_20)),
        'prv_pnn50_pct': float(100 * np.mean(over_50)),
        'prv_cvnn': sdnn / mean,
        'prv_cvsd': rmssd / mean,
        # Natural logarithm of the numerical RMSSD in milliseconds; zero is undefined.
        'prv_lnrmssd': float(np.log(rmssd)) if rmssd > 0 else np.nan,
    }


def calculate_svri(*, map_mmhg, cvp_mmhg, cardiac_index_l_min_m2=None,
                   cardiac_output_l_min=None, bsa_m2=None):
    """SVRi = 80*(MAP-CVP)/CI, in dyn*s*cm^-5*m^2.

    Supply either measured cardiac index, or measured CO and body surface area.
    CVP is mandatory, may be negative, and is never silently assumed to be zero.
    """
    if not np.isfinite(map_mmhg) or map_mmhg <= 0:
        raise ValueError('MAP must be finite and positive (mmHg)')
    if not np.isfinite(cvp_mmhg) or cvp_mmhg >= map_mmhg:
        raise ValueError('CVP must be finite and less than MAP (mmHg)')
    if cardiac_index_l_min_m2 is not None:
        if cardiac_output_l_min is not None or bsa_m2 is not None:
            raise ValueError('Supply cardiac index OR cardiac output with BSA, not both')
        ci = cardiac_index_l_min_m2
    else:
        if cardiac_output_l_min is None or bsa_m2 is None:
            raise ValueError('Supply cardiac index or both cardiac output and BSA')
        if not np.isfinite(cardiac_output_l_min) or cardiac_output_l_min <= 0:
            raise ValueError('Cardiac output must be finite and positive (L/min)')
        if not np.isfinite(bsa_m2) or bsa_m2 <= 0:
            raise ValueError('BSA must be finite and positive (m^2)')
        ci = cardiac_output_l_min / bsa_m2
    if not np.isfinite(ci) or ci <= 0:
        raise ValueError('Cardiac index must be finite and positive (L/min/m^2)')
    return float(80 * (map_mmhg - cvp_mmhg) / ci)


def hemodynamics_from_args(args):
    """Validate optional CLI measurements before processing any recording."""
    fields = ('map_mmhg', 'cvp_mmhg', 'cardiac_index', 'cardiac_output', 'bsa')
    values = {field: getattr(args, field, None) for field in fields}
    row = {
        'map_mmhg': values['map_mmhg'], 'cvp_mmhg': values['cvp_mmhg'],
        'cardiac_index_l_min_m2': values['cardiac_index'],
        'cardiac_output_l_min': values['cardiac_output'], 'bsa_m2': values['bsa'],
        'svri_dyn_s_cm5_m2': np.nan, 'svri_units': 'dyn*s*cm^-5*m^2',
        'svri_status': 'unavailable: requires external MAP, CVP, and CI (or CO and BSA)',
        'source': 'external measurements; not derived from PPG',
    }
    if all(value is None for value in values.values()):
        return row
    if args.map_mmhg is None or args.cvp_mmhg is None:
        raise ValueError('SVRi requires both --map-mmhg and --cvp-mmhg')
    row['svri_dyn_s_cm5_m2'] = calculate_svri(
        map_mmhg=args.map_mmhg, cvp_mmhg=args.cvp_mmhg,
        cardiac_index_l_min_m2=args.cardiac_index,
        cardiac_output_l_min=args.cardiac_output, bsa_m2=args.bsa)
    if args.cardiac_index is None:
        row['cardiac_index_l_min_m2'] = args.cardiac_output / args.bsa
    row['svri_status'] = 'calculated from supplied external measurements'
    return row
