"""La stack de production : ce qui est exposé, et à qui."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def compose():
    return yaml.safe_load((ROOT / "compose.prod.yaml").read_text(encoding="utf-8"))


def test_only_the_proxy_publishes_ports():
    services = compose()["services"]
    published = {name for name, service in services.items() if service.get("ports")}
    assert published == {"caddy"}
    assert services["caddy"]["ports"] == ["80:80", "443:443", "8443:8443"]


def test_every_service_restarts_and_drops_privileges():
    services = compose()["services"]
    for name, service in services.items():
        assert service.get("security_opt") == ["no-new-privileges:true"], name
        if name != "migrate":
            assert service.get("restart") == "unless-stopped", name


def test_the_management_is_reserved_to_the_local_network():
    caddyfile = (ROOT / "deploy" / "Caddyfile").read_text(encoding="utf-8")
    public = caddyfile.split("{$SITE_DOMAIN} {", 1)[1].split("https://{$LAN_HOST}:8443", 1)[0]
    assert "not remote_ip private_ranges" in public
    for path in ("/gestion/*", "/ws/*", "/api/generic/*"):
        assert path in public
    assert public.index("respond @reserve") < public.index("import application")
    local = caddyfile.split("https://{$LAN_HOST}:8443", 1)[1]
    assert "@exterieur not remote_ip private_ranges" in local
    assert "tls internal" in local


def test_production_secrets_stay_out_of_git_and_images():
    for name in (".gitignore", ".dockerignore"):
        assert ".env.prod" in (ROOT / name).read_text(encoding="utf-8")
