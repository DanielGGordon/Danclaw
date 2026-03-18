"""Tests to validate docker-compose.yml and Dockerfile exist and are well-formed."""

import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_docker_compose_exists():
    assert (ROOT / "docker-compose.yml").is_file()


def test_dockerfile_exists():
    assert (ROOT / "Dockerfile").is_file()


def test_docker_compose_has_dispatcher_service():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    assert "dispatcher" in compose["services"]


def test_dispatcher_has_env_file():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    dispatcher = compose["services"]["dispatcher"]
    assert ".env" in dispatcher["env_file"]


def test_dispatcher_has_sqlite_volume():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    dispatcher = compose["services"]["dispatcher"]
    volume_mounts = dispatcher["volumes"]
    assert any("sqlite-data" in v for v in volume_mounts)


def test_sqlite_data_volume_defined():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    assert "sqlite-data" in compose["volumes"]
