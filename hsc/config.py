"""Compaction range (low threshold + auto-compact backstop): per project by default, optional global."""
import json
import os
import tempfile

DEFAULT_LOW = 40
DEFAULT_BACKSTOP = 60
MIN_GAP = 10
BOUNDS = (10, 90)
PRESETS = {"eager": (30, 50), "balanced": (40, 60), "relaxed": (50, 70)}


def global_path():
    base = os.environ.get("HSC_CONFIG_DIR") or os.path.expanduser("~/.config/herdr-smart-compaction")
    return os.path.join(base, "config.json")


def project_path(project_dir):
    return os.path.join(project_dir, ".claude", "hsc.json")


def validate(low, backstop):
    for name, v in (("low", low), ("backstop", backstop)):
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError("%s must be a whole number" % name)
        if not BOUNDS[0] <= v <= BOUNDS[1]:
            raise ValueError("%s must be between %d and %d" % (name, *BOUNDS))
    if backstop - low < MIN_GAP:
        raise ValueError("backstop must be at least %d points above low (got %d-%d)" % (MIN_GAP, low, backstop))
    return low, backstop


def read(path):
    """Return (low, backstop) from a config file, or None if missing/invalid."""
    try:
        with open(path) as f:
            d = json.load(f)
        return validate(d.get("low"), d.get("backstop"))
    except (OSError, ValueError, AttributeError):
        return None


def write(path, low, backstop):
    validate(low, backstop)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".hsc-cfg.", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump({"low": low, "backstop": backstop}, f)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def resolve(project_dir=None, env=None, gpath=None):
    """Effective range: env HSC_LOW_PCT (low only) > project file > global file > defaults."""
    env = os.environ if env is None else env
    low, backstop, source = DEFAULT_LOW, DEFAULT_BACKSTOP, "default"
    g = read(gpath or global_path())
    if g:
        (low, backstop), source = g, "global"
    p = read(project_path(project_dir)) if project_dir else None
    if p:
        (low, backstop), source = p, "project"
    try:
        if env.get("HSC_LOW_PCT"):
            low, source = float(env["HSC_LOW_PCT"]), "env"
    except ValueError:
        pass
    return {"low": low, "backstop": backstop, "source": source}


def tokens(pct, window):
    return "%dK" % round(window * pct / 100 / 1000) if isinstance(window, (int, float)) and window else None
