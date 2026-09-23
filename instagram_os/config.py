"""Brand resolution and configuration loading.

Brand folders live outside this repo and are resolved through
`.claude/brands.local.json` (same registry every skill uses). Per-brand Instagram
config lives at `<brand-path>/instagram/config.yaml` and is deep-merged over
`instagram_os/defaults.yaml`. Secrets only ever come from environment variables.
"""
import copy
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULTS_PATH = PACKAGE_DIR / "defaults.yaml"

AUTONOMY_VALUES = {"auto", "approval", "human", "off"}
# The official API cannot do these, so no config can make them autonomous.
LOCKED_HUMAN_ACTIONS = {"third_party_comment", "like_post", "follow_account", "send_first_dm"}


class ConfigError(Exception):
    pass


def repo_root():
    here = PACKAGE_DIR.parent
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=here,
                             capture_output=True, text=True, check=True)
        return Path(out.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return here


def resolve_brand_path(slug, registry_path=None):
    registry_path = Path(registry_path or os.environ.get("BRANDS_REGISTRY")
                         or repo_root() / ".claude" / "brands.local.json")
    if not registry_path.exists():
        raise ConfigError(f"No brand registry at {registry_path}. Run the brand-onboarding skill first.")
    registry = json.loads(registry_path.read_text())
    if slug not in registry:
        raise ConfigError(f"Brand '{slug}' is not registered in {registry_path}. "
                          f"Registered: {', '.join(sorted(registry)) or '(none)'}")
    path = Path(registry[slug]).expanduser()
    if not path.is_dir():
        raise ConfigError(f"Brand folder for '{slug}' does not exist: {path}")
    return path


def deep_merge(base, override):
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


@dataclass
class Secrets:
    """Credentials read from the environment. Never logged, never persisted."""
    access_token: str = ""
    account_id: str = ""
    app_id: str = ""
    app_secret: str = ""
    anthropic_api_key_present: bool = False

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        return cls(
            access_token=env.get("INSTAGRAM_ACCESS_TOKEN", "").strip(),
            account_id=env.get("INSTAGRAM_ACCOUNT_ID", "").strip(),
            app_id=env.get("META_APP_ID", "").strip(),
            app_secret=env.get("META_APP_SECRET", "").strip(),
            anthropic_api_key_present=bool(env.get("ANTHROPIC_API_KEY")),
        )

    @property
    def has_credentials(self):
        return bool(self.access_token and self.account_id)

    def __repr__(self):  # keep tokens out of tracebacks and logs
        return (f"Secrets(access_token={'***' if self.access_token else ''}, "
                f"account_id={self.account_id!r}, app_id={self.app_id!r}, "
                f"app_secret={'***' if self.app_secret else ''})")


@dataclass
class BrandContext:
    slug: str
    path: Path
    config: dict
    secrets: Secrets

    @property
    def instagram_dir(self):
        return self.path / "instagram"

    @property
    def content_dir(self):
        return self.path / "content-packages"

    @property
    def db_path(self):
        return self.instagram_dir / "state.db"

    @property
    def live(self):
        return self.config["api"]["mode"] == "live"

    def autonomy(self, action):
        if action in LOCKED_HUMAN_ACTIONS:
            return "human"
        value = self.config["autonomy"].get(action, "approval")
        return value

    def brand_file(self, name):
        p = self.path / name
        return p.read_text() if p.exists() else ""


def load_config(brand_path, env=None):
    env = os.environ if env is None else env
    defaults = yaml.safe_load(DEFAULTS_PATH.read_text())
    brand_cfg_path = Path(brand_path) / "instagram" / "config.yaml"
    brand_cfg = yaml.safe_load(brand_cfg_path.read_text()) if brand_cfg_path.exists() else {}
    cfg = deep_merge(defaults, brand_cfg or {})

    # Environment overrides — change autonomy without touching files.
    if env.get("PUBLISH_MODE"):
        cfg["autonomy"]["publish_post"] = env["PUBLISH_MODE"].strip().lower()
    if env.get("INSTAGRAM_API_MODE"):
        cfg["api"]["mode"] = env["INSTAGRAM_API_MODE"].strip().lower()
    if env.get("INSTAGRAM_LOGIN_TYPE"):
        cfg["api"]["login_type"] = env["INSTAGRAM_LOGIN_TYPE"].strip().lower()

    validate_config(cfg)
    return cfg


def validate_config(cfg):
    if cfg["api"]["mode"] not in {"dry_run", "live"}:
        raise ConfigError("api.mode must be dry_run or live")
    if cfg["api"]["login_type"] not in {"instagram", "facebook"}:
        raise ConfigError("api.login_type must be instagram or facebook")
    for action, value in cfg["autonomy"].items():
        if value not in AUTONOMY_VALUES:
            raise ConfigError(f"autonomy.{action}={value!r}; expected one of {sorted(AUTONOMY_VALUES)}")
    t = cfg["engagement"]["thresholds"]
    if not 0 <= t["suggest"] <= t["auto_reply"] <= 1:
        raise ConfigError("engagement.thresholds must satisfy 0 <= suggest <= auto_reply <= 1")
    if cfg["media_host"]["type"] not in {"none", "url_prefix", "command"}:
        raise ConfigError("media_host.type must be none, url_prefix or command")


def load_brand(slug, registry_path=None, env=None, require_enabled=True):
    env = os.environ if env is None else env
    path = resolve_brand_path(slug, registry_path)
    cfg = load_config(path, env)
    if require_enabled and not cfg.get("enabled"):
        raise ConfigError(
            f"The Instagram layer is not enabled for '{slug}'. Run `instagram --brand {slug} init`, "
            f"review {path / 'instagram' / 'config.yaml'}, then set enabled: true.")
    return BrandContext(slug=slug, path=path, config=cfg, secrets=Secrets.from_env(env))
