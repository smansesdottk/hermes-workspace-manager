#!/usr/bin/env python3
"""Pemeriksaan regresi integritas image Docker dan bebas Antigravity."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_new_dockerman_image_is_agy_free():
    dockerfile = open("/home/malik/docker-ai-stack/hermes-agent/Dockerfile").read()
    assert "antigravity-cli" not in dockerfile
    assert "/usr/local/bin/agy" not in dockerfile
