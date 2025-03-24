import pytest
from fastapi.testclient import TestClient

from delamain.app import app as APP


@pytest.fixture
def app():
    APP.dependency_overrides = {}
    yield APP


@pytest.fixture
def client_header():
    headers = {}

    return headers


@pytest.fixture
def client(app, client_header):
    with TestClient(
        app,
        headers=client_header,
    ) as client:
        yield client
