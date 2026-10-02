# SPDX-License-Identifier: GPL-3.0-or-later
"""Guide (landmark) definitions and storage on the scan object."""

import json

from . import face_template
from . import hand_builder

PROP = "auto_retopo_guides"


def definitions(kind, symmetric=True):
    """[(id, Japanese label, English label)] in click order."""
    if kind == 'FACE':
        out = []
        for lid, _p, paired, ja, en in face_template.LANDMARKS:
            if paired:
                if symmetric:
                    out.append((lid, ja + "（片側）", en + " (one side)"))
                else:
                    out.append((lid, ja + "（右）", en + " (right)"))
            else:
                out.append((lid, ja, en))
        if not symmetric:
            for lid, _p, paired, ja, en in face_template.LANDMARKS:
                if paired:
                    out.append((lid + "_m", ja + "（左）", en + " (left)"))
        return out
    return hand_builder.landmarks()


def load(obj, kind):
    try:
        data = json.loads(obj.get(f"{PROP}_{kind}", "{}"))
    except (TypeError, ValueError):
        data = {}
    return {k: tuple(v) for k, v in data.items()}


def save(obj, kind, guides):
    obj[f"{PROP}_{kind}"] = json.dumps({k: [float(x) for x in v] for k, v in guides.items()})
