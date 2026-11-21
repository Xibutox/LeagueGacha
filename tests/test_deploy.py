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
        if name not in {"migrate", "recalcul"}:
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


def test_the_pi_waits_for_the_ci_job_it_names():
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    triggers = workflow.get("on") or workflow.get(True)  # « on » lu comme True par YAML 1.1
    assert "v*" in triggers["push"]["tags"]
    assert "tests" in workflow["jobs"]
    script = (ROOT / "deploy" / "raspberry" / "deploiement-auto.sh").read_text(encoding="utf-8")
    assert "check_name=tests" in script
    assert "sauvegarde.sh" in script and "healthy" in script


def test_startup_creates_the_booster_types():
    migrate = compose()["services"]["migrate"]["command"]
    assert "migrate --noinput" in migrate and "init_boosters" in migrate
    assert "recompute_cards" not in migrate  # le site n'attend pas le recalcul
    services = compose()["services"]
    assert "recompute_cards --si-besoin" in services["recalcul"]["command"]
    assert "recalcul" not in services["web"].get("depends_on", {})


def test_the_server_scripts_are_executable_and_unix():
    for script in (ROOT / "deploy" / "raspberry").glob("*.sh"):
        assert script.stat().st_mode & 0o111, script.name
        assert b"\r\n" not in script.read_bytes(), script.name
